# 你的 pak_tool 修复指南（实测结论）

## 结论：你的旧工具【核心功能完全可用】
- `pak_tool unpack res.pak -o out` → 913 个文件全部正确解出 ✅
- `pak_tool pack out -o new.pak`  → 回封成功，大小精确一致 ✅
- 去年失败的原因 = **回封时 2 个头部字段写错**，现已定位并给出修复

## 修复点 1：`unk` 字段（@8）
真实含义（5 个 pak 全部验证成立）：
```
unk == filesize - headerSize      // 即数据区大小
```
实例：
| pak | headerSize | unk | filesize | 验证 |
|---|---|---|---|---|
| res.pak  | 33880  | 409803807 | 409837687 | ✅ |
| res1.pak | 13641  | 269676276 | 269689917 | ✅ |
| res2.pak | 4084   | 439678276 | 439682360 | ✅ |
| res3.pak | 5734   | 323876507 | 323882241 | ✅ |
| res4.pak | 184016 | 219414995 | 219599011 | ✅ |

**你的 pak_tool 回封时写的是 0 → 游戏会拒绝。** 需改为 `filesize - headerSize`。

## 修复点 2：`stamp` 哈希（@12，64 字节 ASCII）
你的 PakBuilder.kt 硬编码的是**过期的**：
```
旧(错误): 24c4cd875259aeec5bda6be2620aae546e4e3aae44b2f1293ff0d649bee1cce0
新(正确): be9b51cab13f0a0c0f5ccf0512d5e2f6f8661b771743b050d1be108ed2460c7f
```
当前 **5 个 pak 的 stamp 完全相同**，游戏用 `PAK_STAMP_HASH` 做全局校验，
不匹配就**直接拒绝整个 pak**（见你文档 §5）。

## 修复点 3：条目顺序（非致命但建议对齐）
- 原始 pak 保留**原始写入顺序**
- 回封按目录扫描顺序（字典序），导致目录树字节不同
- 游戏按路径查找，顺序无关 → **可以不改**，但若追求字节级一致需保留顺序

## 完整的回封后处理（Python 一行修好）
```python
import struct, os
p = 'new.pak'
orig_stamp = open('res.pak','rb').read(76)[12:76]   # 原始 stamp
sz = os.path.getsize(p)
with open(p,'r+b') as f:
    hs = struct.unpack_from('<I', f.read(76), 4)[0]
    f.seek(8);  f.write(struct.pack('<I', sz - hs))  # 修正 unk
    f.seek(12); f.write(orig_stamp)                  # 修正 stamp
```

## PAK 格式速查（已全部实测确认）
```
头 76 字节:
  @0  3  "PAK"
  @3  1  version = 1
  @4  4  headerSize (u32 LE) = 33880
  @8  4  dataSize   (u32 LE) = filesize - headerSize
  @12 64 stamp (ASCII hex, PAK_STAMP_HASH)

@76 .. headerSize : 目录树
  Entry { u8 nameLen; char name[]; u8 flags;
          if flags&1: u32 childCount; childCount * Entry
          else: if flags&2: f64 pos; else: u32 pos;
                u32 size; u32 extra; }

数据区 @headerSize :
  实际偏移 = headerSize + pos      ← 关键
  裸数据，未压缩未加密（PNG/JSON/BATL 原样）
```
