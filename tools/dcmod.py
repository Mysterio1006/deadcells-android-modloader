#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dcmod.py — 重生细胞 (Dead Cells) Android PAK 工具链 / mod 构建器

实测确认的 PAK 格式 (全部字段已交叉验证):
  头 76 字节:
    @0   3  magic "PAK"
    @3   1  version = 1
    @4   4  headerSize (u32 LE)
    @8   4  dataSize   (u32 LE) = filesize - headerSize   <<< pak_tool 写 0 的 bug
    @12 64  stamp (ASCII hex) = PAK_STAMP_HASH             <<< pak_tool 写过期值的 bug
  @76 .. headerSize : 目录树
    Entry { u8 nameLen; char name[]; u8 flags;
            flags&1 : u32 childCount; childCount * Entry
            else    : (flags&2 ? f64 : u32) pos; u32 size; u32 extra; }
  数据区 @headerSize : 裸数据, 未压缩未加密
    实际偏移 = headerSize + pos

用法:
  dcmod.py info   <pak>                  # 打印头部与自校验
  dcmod.py list   <pak>                  # 列出目录树条目
  dcmod.py stamp  <pak>                  # 打印 stamp 哈希
  dcmod.py fix    <pak> [--stamp-from X] # 修复 dataSize + stamp (原地, 先备份)
  dcmod.py extract <pak> <name> <out>    # 按路径提取单个文件 (绕过整包解包)
  dcmod.py verify <pak>                  # 校验目录树 offset 平铺, 无缝隙/无重叠
"""
import struct, sys, os, shutil, json

HDR = 76

def read_header(path):
    with open(path, 'rb') as f:
        b = f.read(HDR)
    if len(b) < HDR:
        raise ValueError("file too small for PAK header")
    magic = b[0:3]
    ver = b[3]
    header_size, data_size = struct.unpack_from('<II', b, 4)
    stamp = b[12:76]
    return {
        'magic': magic, 'version': ver,
        'header_size': header_size, 'data_size': data_size,
        'stamp': stamp,
        'stamp_str': stamp.decode('ascii', 'replace'),
    }

def parse_tree(path):
    """解析目录树, 返回 (entries, bytes_used)"""
    h = read_header(path)
    hs = h['header_size']
    with open(path, 'rb') as f:
        f.seek(HDR)
        buf = f.read(hs - HDR)
    entries = []
    pos = [0]

    def rd(n):
        v = buf[pos[0]:pos[0] + n]
        pos[0] += n
        return v

    def entry(path_prefix):
        nl = rd(1)[0]
        name = rd(nl).decode('utf-8', 'replace')
        flags = rd(1)[0]
        full = (path_prefix + '/' + name) if path_prefix else name
        if flags & 1:
            cnt = struct.unpack('<I', rd(4))[0]
            node = {'name': name, 'path': full, 'flags': flags,
                    'is_dir': True, 'children': []}
            for _ in range(cnt):
                node['children'].append(entry(full))
            return node
        else:
            if flags & 2:
                p = struct.unpack('<d', rd(8))[0]
            else:
                p = struct.unpack('<I', rd(4))[0]
            size, extra = struct.unpack('<II', rd(8))
            node = {'name': name, 'path': full, 'flags': flags,
                    'is_dir': False, 'pos': p, 'size': size, 'extra': extra}
            return node

    # 根节点实测布局 (offset 76):
    #   @76  u8 x=0 ; @77 u8 y=1 ; @78 u8 topCount (=顶层目录记录数)
    #        @79 u24 = 0 (padding)
    #   然后连续 topCount 个顶层目录 Entry
    #   实测: res.pak topCount=1 ("atlas"), res4.pak topCount=45 (cinematics, ...)
    x = rd(1)[0]                            # 0
    y = rd(1)[0]                            # 1
    top_count = rd(1)[0]                    # 顶层目录数
    rd(3)                                   # 000000 padding
    root = {'name': '', 'path': '', 'flags': 1, 'is_dir': True,
            'children': [], 'top_count': top_count}
    for _ in range(top_count):
        root['children'].append(entry(''))
    return root, pos[0], buf

def flatten(node_or_list):
    """只取文件节点 (接受 root 节点 或 root 的 children 列表)"""
    out = []
    items = node_or_list['children'] if isinstance(node_or_list, dict) else node_or_list
    for e in items:
        if e.get('is_dir'):
            out.extend(flatten(e))
        else:
            out.append(e)
    return out

def cmd_info(path):
    h = read_header(path)
    sz = os.path.getsize(path)
    print(f"file        : {path}")
    print(f"size        : {sz:,}")
    print(f"magic       : {h['magic']!r} version={h['version']}")
    print(f"headerSize  : {h['header_size']:,}")
    print(f"dataSize    : {h['data_size']:,}")
    print(f"expected    : {sz - h['header_size']:,}  (= size - headerSize)")
    ok = (h['data_size'] == sz - h['header_size'])
    print(f"dataSize OK : {ok}")
    print(f"stamp       : {h['stamp_str']}")

def cmd_stamp(path):
    print(read_header(path)['stamp_str'])

def cmd_list(path):
    entries, used, _ = parse_tree(path)
    files = flatten(entries)
    print(f"entries={len(entries)} files={len(files)} tree_bytes_used={used:,}")
    for e in files:
        print(f"  {e['size']:>12,}  pos={e['pos']:>12,}  {e['path']}")

def cmd_verify(path):
    entries, used, _ = parse_tree(path)
    files = sorted(flatten(entries), key=lambda e: e['pos'])
    h = read_header(path)
    gaps, overlaps = [], []
    prev_end = None
    for e in files:
        if prev_end is not None:
            d = e['pos'] - prev_end
            if d != 0:
                (gaps if d > 0 else overlaps).append((prev_end, e['pos'], d))
        prev_end = e['pos'] + e['size']
    print(f"files={len(files)} tree_bytes_used={used:,} headerSize={h['header_size']:,}")
    print(f"offset tiling: gaps={len(gaps)} overlaps={len(overlaps)}")
    if gaps[:5]: print("  first gaps:", gaps[:5])
    if overlaps[:5]: print("  first overlaps:", overlaps[:5])
    print(f"data span end={prev_end:,}  dataSize={h['data_size']:,}  match={prev_end == h['data_size']}")

def cmd_extract(path, name, out):
    entries, used, _ = parse_tree(path)
    files = flatten(entries)
    norm = name.strip('/')
    for e in files:
        if e['path'] == norm or e['name'] == norm:
            h = read_header(path)
            off = h['header_size'] + e['pos']
            with open(path, 'rb') as f:
                f.seek(off)
                data = f.read(e['size'])
            with open(out, 'wb') as g:
                g.write(data)
            print(f"extracted {e['path']} size={len(data):,} -> {out}")
            return
    print(f"NOT FOUND: {name}", file=sys.stderr)
    sys.exit(2)

def cmd_fix(path, stamp_from=None):
    h = read_header(path)
    sz = os.path.getsize(path)
    if stamp_from:
        src = read_header(stamp_from)['stamp']
    else:
        src = h['stamp']  # 保留原 stamp
    bak = path + '.bak'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"backup -> {bak}")
    with open(path, 'r+b') as f:
        f.seek(8)
        f.write(struct.pack('<I', sz - h['header_size']))
        f.seek(12)
        f.write(src)
    print(f"fixed {path}: dataSize={sz - h['header_size']:,} stamp={src.decode('ascii','replace')}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    c = sys.argv[1]
    a = sys.argv[2:]
    if c == 'info': cmd_info(a[0])
    elif c == 'stamp': cmd_stamp(a[0])
    elif c == 'list': cmd_list(a[0])
    elif c == 'verify': cmd_verify(a[0])
    elif c == 'extract': cmd_extract(a[0], a[1], a[2])
    elif c == 'fix':
        sf = None
        if '--stamp-from' in a:
            i = a.index('--stamp-from'); sf = a[i+1]
        cmd_fix(a[0], sf)
    else:
        print(__doc__); sys.exit(1)
