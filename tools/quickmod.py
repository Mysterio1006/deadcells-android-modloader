#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速改 mod —— 改 data.cdb 里的一个字段，自动出可安装的 APK。

用法:
  python3 quickmod.py <cdb路径> <表名> <行id> <字段> <新值> [--apk-out 输出]

例:
  # 金币收益 99 倍
  python3 quickmod.py data.cdb difficulty Normal goldRatio 99

  # 某武器造价改成 1
  python3 quickmod.py data.cdb item HorizontalTurret moneyCost 1

  # 普通难度细胞掉落 10 倍
  python3 quickmod.py data.cdb difficulty Normal cellDropMultiplier 10

说明:
  - 表名见 dcmod.py list 或本目录 MOD_GUIDE.md
  - 值会自动判断类型 (int/float/list)
  - 输出会自动完成: 打包 res5.pak -> 修头 -> 追加进 APK -> 重签名
"""
import json, sys, os, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def coerce(v):
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v.lower() in ('true', 'false'):
        return v.lower() == 'true'
    if v.startswith('[') and v.endswith(']'):
        return json.loads(v)
    return v


def main():
    if len(sys.argv) < 6:
        print(__doc__); sys.exit(1)
    cdb, sheet, rowid, field, value = sys.argv[1:6]
    apk_out = 'mod_quick.apk'
    if '--apk-out' in sys.argv:
        apk_out = sys.argv[sys.argv.index('--apk-out') + 1]

    d = json.load(open(cdb))
    hit = False
    for s in d['sheets']:
        if s['name'] != sheet:
            continue
        for l in s['lines']:
            if str(l.get('id')) == rowid:
                old = l.get(field, '<不存在>')
                l[field] = coerce(value)
                print(f"[{sheet}] {rowid}.{field}: {old} -> {l[field]}")
                hit = True
    if not hit:
        print(f"未找到: sheet={sheet} id={rowid}", file=sys.stderr); sys.exit(2)

    # 写出
    mod_cdb = os.path.join(HERE, 'work_test', '_quick.cdb')
    open(mod_cdb, 'w').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))

    # 组装 res5.pak
    d5 = os.path.join(HERE, 'mod5_quick', 'res5_pak')
    shutil.rmtree(os.path.join(HERE, 'mod5_quick'), ignore_errors=True)
    os.makedirs(d5, exist_ok=True)
    shutil.copy2(mod_cdb, os.path.join(d5, 'data.cdb'))

    pak = os.path.join(HERE, 'work_test', '_quick_res5.pak')
    base = os.path.join(HERE, 'work', 'base.apk')
    ref = os.path.join(HERE, 'res4.pak')
    tmp_apk = os.path.join(HERE, 'work', '_quick_unsigned.apk')

    for cmd in (
        [sys.executable, os.path.join(HERE, 'dcmake.py'), 'pack',
         os.path.join(HERE, 'mod5_quick', 'res5_pak'), pak, '--stamp-from', ref],
        [sys.executable, os.path.join(HERE, 'dcmake.py'), 'apk-append',
         base, tmp_apk, pak, 'assets/res5.pak'],
        [sys.executable, os.path.join(HERE, 'dcmake.py'), 'sign',
         tmp_apk, os.path.join(HERE, apk_out)],
    ):
        r = subprocess.run(cmd, env={**os.environ,
                                     'PREFIX': os.environ.get('PREFIX', '')})
        if r.returncode != 0:
            print('FAILED:', ' '.join(cmd), file=sys.stderr); sys.exit(3)

    out = os.path.join(HERE, apk_out)
    print(f"\n✅ 成品: {out}  ({os.path.getsize(out):,} bytes)")
    print("安装:")
    print(f"  cp {out} /data/local/tmp/m.apk")
    print("  cmd package install -r -d /data/local/tmp/m.apk")


if __name__ == '__main__':
    main()
