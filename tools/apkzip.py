#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apkzip.py — 把 pak 换进 APK 的「任意大小」重建器

两种模式:
  1. inplace : 新 pak 与旧 pak 等长 -> 原地改写 + 更新 CRC (最快, 不重建)
  2. rebuild : 大小不同 -> 重建 ZIP, 保持其他条目字节不变, 自动处理偏移

rebuild 的关键: APK 的 assets 是 STORED, 其余条目多为 DEFLATED。
我们逐条重新写出, 数据从原 APK 流式拷贝(不解压), 只替换目标条目。
这样避免了解压 1.78GB 的开销, 也保证其他条目字节完全一致。

用法:
  apkzip.py swap <base.apk> <out.apk> <new.pak> <assets/res4.pak>
"""
import os, sys, struct, shutil, zlib

LFH = b'PK\x03\x04'
CDH = b'PK\x01\x02'
EOCD = b'PK\x05\x06'

def read_central(path):
    """返回 (entries, cd_offset, cd_size, eocd_offset)"""
    sz = os.path.getsize(path)
    with open(path, 'rb') as f:
        back = min(sz, 65536 + 22)
        f.seek(sz - back)
        tail = f.read(back)
        i = tail.rfind(EOCD)
        if i < 0:
            raise ValueError('EOCD not found')
        eocd_abs = sz - back + i
        cd_size, cd_off = struct.unpack_from('<II', tail, i + 12)[0], struct.unpack_from('<I', tail, i + 16)[0]
        f.seek(cd_off)
        data = f.read(cd_size)
    entries = []
    p = 0
    while p < len(data):
        if data[p:p+4] != CDH:
            break
        (sig, ver, verneed, flag, method, mtime, mdate, crc, csize,
         usize, nl, el, cl, disk, iattr, eattr, lho) = struct.unpack_from('<IHHHHHHIIIHHHHHII', data, p)
        name = data[p+46:p+46+nl]
        extra = data[p+46+nl:p+46+nl+el]
        comment = data[p+46+nl+el:p+46+nl+el+cl]
        entries.append(dict(name=name, flag=flag, method=method, mtime=mtime,
                            mdate=mdate, crc=crc, csize=csize, usize=usize,
                            extra=extra, comment=comment, lho=lho,
                            verneed=verneed, iattr=iattr, eattr=eattr))
        p += 46 + nl + el + cl
    return entries, cd_off, cd_size, eocd_abs

def write_local_header(f, e, name, crc, csize, usize, lho, extra):
    hdr = struct.pack('<IHHHHHIIIHH', 0x04034b50, 20, e['flag'], e['method'],
                      e['mtime'], e['mdate'], crc, csize, usize,
                      len(name), len(extra))
    f.write(hdr); f.write(name); f.write(extra)

def write_central(f, e, name, crc, csize, usize, lho, extra):
    hdr = struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, 20, e['verneed'],
                      e['flag'], e['method'], e['mtime'], e['mdate'],
                      crc, csize, usize, len(name), len(extra),
                      len(e['comment']), 0, e['iattr'], e['eattr'], lho)
    f.write(hdr); f.write(name); f.write(extra); f.write(e['comment'])

def swap(base, out, newpak, target_name):
    name = target_name.encode()
    entries, cd_off, cd_size, eocd_abs = read_central(base)
    tgt = [e for e in entries if e['name'] == name]
    if not tgt:
        raise SystemExit(f'target not found: {target_name}')
    tgt = tgt[0]
    new_size = os.path.getsize(newpak)
    old_size = tgt['usize']

    # 计算新 CRC（流式）
    crc = 0
    with open(newpak, 'rb') as g:
        while True:
            b = g.read(1 << 20)
            if not b:
                break
            crc = zlib.crc32(b, crc)
    crc &= 0xffffffff

    if new_size == old_size and tgt['method'] == 0:
        print(f'[inplace] size unchanged ({new_size:,}), 原地替换')
        shutil.copy2(base, out)
        with open(out, 'r+b') as f:
            data_off = tgt['lho'] + 30 + len(name) + len(tgt['extra'])
            with open(newpak, 'rb') as g:
                f.seek(data_off)
                while True:
                    b = g.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            f.seek(tgt['lho'] + 14); f.write(struct.pack('<I', crc))
            # central header 位置需重扫
            entries2, cd_off2, _, _ = read_central(out)
            for e in entries2:
                if e['name'] == name:
                    f.seek(cd_off2); break
            # 重新定位该条目的 CRC 字段
            f.seek(cd_off2)
            p = cd_off2
            while True:
                h = f.read(46)
                if len(h) < 46 or h[:4] != CDH:
                    break
                nl, el, cl = struct.unpack_from('<HHH', h, 28)
                nm = f.read(nl)
                f.read(el + cl)
                if nm == name:
                    f.seek(p + 16); f.write(struct.pack('<I', crc))
                    break
                p = f.tell()
        print(f'[inplace] done crc=0x{crc:08x}')
        return

    print(f'[rebuild] size changed {old_size:,} -> {new_size:,}, 重建 ZIP')
    tmp = out + '.tmp'
    with open(base, 'rb') as src, open(tmp, 'wb') as dst:
        new_lho = {}
        for e in entries:
            new_lho[e['name']] = dst.tell()
            nm = e['name']
            if nm == name:
                newcrc, ncsize, nusize = crc, new_size, new_size
                # 目标 pak 用 STORED 写出
                e = dict(e); e['method'] = 0
                e['extra'] = e['extra'][:0]
                write_local_header(dst, e, nm, newcrc, ncsize, nusize, dst.tell(), e['extra'])
                with open(newpak, 'rb') as g:
                    while True:
                        b = g.read(1 << 20)
                        if not b:
                            break
                        dst.write(b)
            else:
                # 原样拷贝: local header + data
                src.seek(e['lho'])
                lh = src.read(30)
                nl, el = struct.unpack_from('<HH', lh, 26)
                lh_name = src.read(nl); lh_extra = src.read(el)
                dst.write(lh); dst.write(lh_name); dst.write(lh_extra)
                src.seek(e['lho'] + 30 + nl + el)
                remain = e['csize']
                while remain > 0:
                    b = src.read(min(1 << 20, remain))
                    if not b:
                        break
                    dst.write(b); remain -= len(b)
        cd_start = dst.tell()
        for e in entries:
            nm = e['name']
            if nm == name:
                e2 = dict(e); e2['method'] = 0; e2['extra'] = b''; e2['csize'] = new_size; e2['usize'] = new_size
                write_central(dst, e2, nm, crc, new_size, new_size, new_lho[nm], b'')
            else:
                write_central(dst, e, nm, e['crc'], e['csize'], e['usize'], new_lho[nm], e['extra'])
        cd_end = dst.tell()
        dst.write(struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, len(entries), len(entries),
                              cd_end - cd_start, cd_start, 0))
    os.replace(tmp, out)
    print(f'[rebuild] done -> {out} ({os.path.getsize(out):,} bytes)')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    if sys.argv[1] == 'swap':
        swap(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        print(__doc__); sys.exit(1)
