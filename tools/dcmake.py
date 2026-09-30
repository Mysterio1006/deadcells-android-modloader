#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dcmake.py — 重生细胞 Android mod 一键流水线 (实测验证版)

把「解包 → 改内容 → 回封 → 修头 → 换进 APK → 重签名」串成一条命令。

依赖:
  - pak_tool      (你的 Rust 工具, unpack/pack)
  - dcmod.py      (PAK 格式修复/校验, dataSize+stamp)
  - apksigner     (Termux: $PREFIX/localbin/apksigner)
  - keystore      ($DC_KEYSTORE, 默认 ~/.android/debug.keystore)

子命令:
  unpack   <pak> <dir>                 解包单个 pak
  pack     <dir> <out.pak> [--stamp-from <ref.pak>]
                                       回封 + 自动修复 dataSize/stamp
  apk-swap <base.apk> <dst.apk> <pakfile> <assetsname>
                                       把 pak 换进 APK (STORED 原地替换, 更新 CRC)
  sign     <in.apk> <out.apk>          用本地 keystore 重签名 (v2)
  verify   <apk>                       apksigner verify

典型流程 (改 data.cdb 数值):
  python3 dcmake.py unpack res4.pak mod4
  # 编辑 mod4/data.cdb ...
  python3 dcmake.py pack mod4 res4_new.pak --stamp-from res4.pak
  python3 dcmake.py apk-swap work/base.apk work/modded.apk res4_new.pak assets/res4.pak
  python3 dcmake.py sign work/modded.apk work/modded-signed.apk
"""
import os, shutil, sys, subprocess, struct, shutil, zlib, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PAK_TOOL = os.path.join(HERE, 'pak_tool')
DCMOD = os.path.join(HERE, 'dcmod.py')
PREFIX = os.environ.get('PREFIX') or os.path.dirname(os.path.dirname(shutil.which('clang') or '/usr/bin/clang'))
APKSIGNER = os.path.join(PREFIX, 'localbin', 'apksigner')
JAVA_HOME = os.path.join(PREFIX, 'lib', 'jvm', 'java-21-openjdk')
KEYSTORE = os.environ.get('DC_KEYSTORE', os.path.expanduser('~/.android/debug.keystore'))
KS_PASS = 'android'
KS_ALIAS = 'dckey'


def run(cmd, **kw):
    print('+', ' '.join(cmd) if isinstance(cmd, list) else cmd)
    return subprocess.run(cmd, **kw)


def unpack(pak, outdir):
    os.makedirs(outdir, exist_ok=True)
    r = run([PAK_TOOL, 'unpack', pak, '-o', outdir])
    return r.returncode == 0


def pack(dirpath, outpak, stamp_from):
    r = run([PAK_TOOL, 'pack', dirpath, '-o', outpak])
    if r.returncode != 0:
        print('pack failed', file=sys.stderr); return False
    # 修复 dataSize + stamp  (pak_tool 的已知 bug)
    args = [sys.executable, DCMOD, 'fix', outpak]
    if stamp_from:
        args += ['--stamp-from', stamp_from]
    r2 = run(args)
    return r2.returncode == 0


def apk_swap(base_apk, dst_apk, pakfile, asset_name):
    """
    在 ZIP 内原地替换 STORED 条目:
      - 新 pak 与旧 pak 大小必须相同 (STORED + 位置不动)
      - 更新 local header 与 central directory 的 CRC32
    若大小不同则报错(需要重建 zip)。
    """
    if not os.path.exists(dst_apk):
        print(f'copy {base_apk} -> {dst_apk}')
        shutil.copy2(base_apk, dst_apk)
    new_size = os.path.getsize(pakfile)

    with open(dst_apk, 'r+b') as f:
        # 定位 EOCD
        f.seek(0, 2); fsz = f.tell()
        back = min(fsz, 65536 + 22)
        f.seek(fsz - back); tail = f.read(back)
        i = tail.rfind(b'PK\x05\x06')
        if i < 0:
            print('EOCD not found', file=sys.stderr); return False
        eocd_abs = fsz - back + i
        cd_off = struct.unpack_from('<I', tail, i + 16)[0]

        # 遍历 central directory 找条目
        f.seek(cd_off)
        found = None
        while True:
            hdr = f.read(46)
            if len(hdr) < 46 or hdr[0:4] != b'PK\x01\x02':
                break
            nl, el, cl = struct.unpack_from('<HHH', hdr, 28)
            name = f.read(nl).decode('utf-8', 'replace')
            f.read(el + cl)
            if name == asset_name:
                found = (f.tell() - nl - el - cl - 46, hdr, name)
                break
        if not found:
            print(f'asset not found in apk: {asset_name}', file=sys.stderr)
            return False
        cd_ent_off, cd_hdr, _ = found
        old_csize = struct.unpack_from('<I', cd_hdr, 20)[0]
        old_usize = struct.unpack_from('<I', cd_hdr, 24)[0]
        lho = struct.unpack_from('<I', cd_hdr, 42)[0]
        method = struct.unpack_from('<H', cd_hdr, 10)[0]

        # 读 local header 定位数据
        f.seek(lho)
        lh = f.read(30)
        nl2, el2 = struct.unpack_from('<HH', lh, 26)
        data_off = lho + 30 + nl2 + el2

        if method != 0:
            print(f'entry is not STORED (method={method}); 需重建 zip', file=sys.stderr)
            return False
        if new_size != old_usize:
            print(f'size mismatch: new={new_size} old={old_usize}; '
                  f'STORED 原地替换要求等长 (可用 rebuild 模式)', file=sys.stderr)
            return False

        # 计算新 CRC
        crc = 0
        with open(pakfile, 'rb') as g:
            while True:
                b = g.read(1 << 20)
                if not b:
                    break
                crc = zlib.crc32(b, crc)
        crc &= 0xffffffff

        # 写数据
        with open(pakfile, 'rb') as g:
            f.seek(data_off)
            while True:
                b = g.read(1 << 20)
                if not b:
                    break
                f.write(b)
        # 更新 local header CRC (offset 14)
        f.seek(lho + 14); f.write(struct.pack('<I', crc))
        # 更新 central header CRC (offset 16)
        f.seek(cd_ent_off + 16); f.write(struct.pack('<I', crc))
    print(f'swapped {asset_name}: crc=0x{crc:08x} size={new_size:,} data_off={data_off:,}')
    return True


def apk_append(base_apk, dst_apk, pakfile, asset_name, rebuild=True):
    """
    以**新增条目**方式把 pak 加进 APK (用户实测工作流: res5.pak)。
    - 保留每个原有条目的压缩方式 (不要全转 STORED, 否则体积暴涨 134MB)
    - 新 pak 用 STORED (游戏要求「仅储存」)
    """
    import zipfile
    tmp = dst_apk + '.tmp'
    zin = zipfile.ZipFile(base_apk, 'r')
    zout = zipfile.ZipFile(tmp, 'w')
    for item in zin.infolist():
        data = zin.read(item.filename)
        zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
        zi.compress_type = item.compress_type
        zi.external_attr = item.external_attr
        zi.internal_attr = item.internal_attr
        zi.create_system = item.create_system
        zout.writestr(zi, data)
    zi = zipfile.ZipInfo(asset_name, date_time=(1981, 1, 1, 1, 1, 2))
    zi.compress_type = zipfile.ZIP_STORED       # 仅储存
    zi.external_attr = 0o100644 << 16
    with open(pakfile, 'rb') as g:
        zout.writestr(zi, g.read())
    zout.close(); zin.close()
    os.replace(tmp, dst_apk)
    print(f'appended {asset_name}: {os.path.getsize(pakfile):,} bytes (STORED) '
          f'-> {dst_apk} ({os.path.getsize(dst_apk):,} bytes)')
    return True


def sign(inp, outp):
    env = dict(os.environ)
    env['JAVA_HOME'] = JAVA_HOME
    env['PATH'] = os.path.join(JAVA_HOME, 'bin') + ':' + env.get('PATH', '')
    r = run([APKSIGNER, 'sign',
             '--ks', KEYSTORE, '--ks-pass', f'pass:{KS_PASS}',
             '--key-pass', f'pass:{KS_PASS}', '--ks-key-alias', KS_ALIAS,
             '--v2-signing-enabled', 'true', '--v1-signing-enabled', 'false',
             '--out', outp, inp], env=env)
    return r.returncode == 0


def verify(apk):
    env = dict(os.environ)
    env['JAVA_HOME'] = JAVA_HOME
    env['PATH'] = os.path.join(JAVA_HOME, 'bin') + ':' + env.get('PATH', '')
    r = run([APKSIGNER, 'verify', '--print-certs', '-v', apk], env=env)
    return r.returncode == 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    c = sys.argv[1]; a = sys.argv[2:]
    if c == 'unpack':
        sys.exit(0 if unpack(a[0], a[1]) else 1)
    elif c == 'pack':
        sf = None
        if '--stamp-from' in a:
            i = a.index('--stamp-from'); sf = a[i + 1]
        sys.exit(0 if pack(a[0], a[1], sf) else 1)
    elif c == 'apk-swap':
        sys.exit(0 if apk_swap(a[0], a[1], a[2], a[3]) else 1)
    elif c == 'apk-append':
        sys.exit(0 if apk_append(a[0], a[1], a[2], a[3]) else 1)
    elif c == 'sign':
        sys.exit(0 if sign(a[0], a[1]) else 1)
    elif c == 'verify':
        sys.exit(0 if verify(a[0]) else 1)
    else:
        print(__doc__); sys.exit(1)
