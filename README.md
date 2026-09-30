# Dead Cells (Android) External Mod Loader

**外载 Mod 加载器 for《重生细胞》安卓版** —— 装一次加载器，之后把 mod 丢进目录即生效，**不用重装、不用改包**。

> An external mod loader for **Dead Cells mobile (Chinese Bilibili release)**.
> Install the loader-bearing APK **once**; afterwards drop mod files into a folder and they take effect with no repackaging.

---

## ⚠️ 重要前提 / Important Caveats

| 项 | 说明 |
|---|---|
| **游戏版本** | **哔哩哔哩(Bilibili)渠道版** `com.bilibili.deadcells.mobile` |
| **实测版本号** | **3.5.9** (versionCode **263**), targetSdk 30 |
| **平台** | **Android 移动端**, arm64-v8a |
| **实测机型** | vivo V2304A / Android 16 (SDK 36) |

> **这是给移动端(bilibili 安卓版)做的**，**不是** Steam/PC 版。
> 版本号很关键: 3.5.9 与 3.5.14 的 **PAK 校验戳不同**，
> 换版本后需要用对应版本重新做资源包(见 [docs/技术文档.md](docs/技术文档.md) §2.4)。
> 其他渠道版(Google Play / TapTap / 国际版)的包名与库结构可能不同，**未经验证**。

### 与原版的关系

本项目**不包含任何游戏资源或游戏本体**。它只提供:
- 一个替换 `libmain.so` 的**加载器源码**
- 一套解析/打包 PAK 的**工具脚本**
- 逆向分析文档

你需要**自己拥有游戏**，用工具从你自己的安装里取出 `res*.pak` 来制作 mod。

---

## 工作原理

游戏启动时 `DeadCellsLoading.onCreate` 通过 ReLinker 加载 `libmain.so`。
把 `libmain.so` 换成我们的加载器后，构造函数就在**游戏进程内**执行:

1. 起一个线程轮询 `dlopen("libnative-lib.so", RTLD_NOLOAD)`
2. 用 `dl_iterate_phdr` + 手动解析 `PT_DYNAMIC` 拿到引擎基址
3. `mprotect` 把 `.got.plt` 改成可写(**RELRO 挡不住同进程自己**)，改写 GOT
4. 接管 `assets_exists` 与 `assets_read` 两个函数

于是引擎加载 `res5.pak` 时会从外部目录读取，而不是从 APK。

```
引擎原始行为                          加载器接管后
─────────────────────────────        ─────────────────────────────
assets_exists("res5.pak")            assets_exists("res5.pak")
   └─ APK 里没有 → false                 └─ 外部有 → 返回 1  ← 骗过闸门
      └─ 循环 break, 不加载                 └─ 继续
                                      assets_read("res5.pak")
                                         └─ fopen 外部文件
                                         └─ 注册进引擎内部表
                                         └─ 返回索引
```

> 关键点: **只 hook `assets_read` 是不够的** —— 引擎会先问 `assets_exists`，
> 返回 false 就直接 `break`，读取函数根本不会被调用。

---

## 使用

### 1. 编译加载器

```bash
# 需要 Android NDK 或 clang + android sysroot
$CC -shared -fPIC -O2 -o libmodsrv.so src/modsrv.c -llog -ldl -lpthread
```

> bionic **没有 `dlinfo`**，所以代码用 `dl_iterate_phdr` + 手动解析 `PT_DYNAMIC`。

### 2. 替换 APK 内的 libmain.so 并签名

```bash
python3 tools/apkzip.py swap 原版.apk 新版.apk libmodsrv.so lib/arm64-v8a/libmain.so
apksigner sign --ks 你的.keystore --out 签名版.apk 新版.apk
```

### 3. 安装（只需一次）

```bash
adb install -r 签名版.apk
```

### 4. 放 mod

```bash
# 外载目录（必须是 App 私有 external 目录，见下方说明）
/sdcard/Android/data/com.bilibili.deadcells.mobile/files/DeadCellsMods/
```

之后进游戏即生效，**加/换 mod 都不用再动 APK**。

用 `tools/modctl.py` 可以省事:

```bash
python3 tools/modctl.py push 我的mod.pak   # 推送
python3 tools/modctl.py list               # 查看
python3 tools/modctl.py log                # 看加载日志
```

### 5. 验证

日志里出现这行就是成功:

```
★★ 外载命中: res5.pak -> .../DeadCellsMods/res5.pak (idx=5, 2434513 B)
```

> 日志写在 `<mod目录>/loader.log`，**不依赖 logcat**
> （游戏启动瞬间日志量极大，会把 logcat 环形缓冲冲掉）。

---

## 两个必须知道的坑

### ① 外载目录不能用 `/sdcard/DeadCellsMods/`

Android 11+ 作用域存储下，即使 manifest 里有 `READ_EXTERNAL_STORAGE`:

| 路径 | stat | open |
|---|---|---|
| `/sdcard/任意目录/` | ✅ | ❌ `EACCES` |
| `/sdcard/Android/data/<包名>/files/...` | ✅ | ✅ **无需任何权限** |

所以必须用 **App 自己的 external files 目录**。

### ② `cmd package clear` 会连 external 目录一起删

顺序**必须**是: **先 clear，再放 mod**。

另外目录需要 setgid 位(`chmod 2777`)，否则游戏进程写的日志 shell 读不出来。

---

## 制作 mod

外载只解决「**怎么加载**」，pak 内容仍要自己改。

引擎是**累加覆盖**语义: 按 `res.pak, res1.pak, res2.pak …` 顺序加载，
**后加载的同路径资源覆盖先加载的**。所以最简单的做法是造一个编号更大的包
（如 `res5.pak`）放改过的 `data.cdb`，**完全不用碰原有 5 个大包**。

```bash
python3 tools/dcmod.py info    res.pak          # 看结构
python3 tools/dcmod.py extract res.pak out/     # 解包
python3 tools/dcmod.py pack    out/ new.pak     # 打包
python3 tools/dcmod.py fix     new.pak --stamp-from res.pak   # 修头 + 校正校验戳
```

`data.cdb` 是游戏的配置表（162 张 sheet），改它就能改数值。

---

## 项目结构

```
src/
  modsrv.c        外载加载器（核心，单文件无依赖）
  probe.c         最小探针（验证构造函数是否执行）
  boom.c          故意缺符号（验证 dlopen 路径）
  kill.c          故意崩溃（验证代码是否真的跑起来）
tools/
  dcmod.py        PAK 解析/解包/打包/校验/修头
  apkzip.py       替换 APK 内文件（等长原地改 / 不等长重建）
  dcmake.py       完整流水线
  modctl.py       外载 mod 管理
  quickmod.py     一键出包
docs/
  技术文档.md      完整技术文档（原理/格式/地址速查/踩坑）
  逆向分析笔记.md   开发过程中的原始分析记录
  PAK格式与改包指南.md
  PAK头修复.md
```

---

## 兼容性

- 已在 **3.5.9 (Bilibili, versionCode 263)** 实测通过
- 3.5.14 的 PAK 结构与 3.5.9 **完全相同**（条目数/headerSize/data.cdb 全一致），
  仅**校验戳不同** —— 工具链无需改动，改包时换 `--stamp-from` 即可
- 3.5.14 新增了腾讯加固库（`libanogs`/`libtapsdkcore`/`libthemis`），
  但 `BIND_NOW` + 全 RELRO 这道墙 **3.5.9 就已存在**，非版本新增；
  且**同进程内可绕过**（见技术文档 §4.1）

---

## 免责声明 / Disclaimer

- 本项目**仅供学习与逆向工程研究**，不含任何游戏资源或游戏本体。
- 请**自行拥有**正版游戏，并自行承担使用风险。
- 修改游戏可能违反用户协议，可能导致账号封禁，请自行评估。
- 《重生细胞》/ Dead Cells 版权归 Motion Twin / Playdigious 所有；
  本项目的作者与上述公司**无任何关联**。
- 代码以 MIT 协议开源（见 [LICENSE](LICENSE)），游戏相关内容不适用。

---

## License

MIT — 见 [LICENSE](LICENSE)
