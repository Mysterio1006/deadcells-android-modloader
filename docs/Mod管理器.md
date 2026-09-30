# Mod 管理器（Android App）

一个可视化的 Mod 管理器：列出、导入、启用/停用、删除 Mod，并查看加载器日志。
**纯 Java + 传统 View，零第三方依赖**（本机没有 Gradle，见下方"如何在设备上构建"）。

---

## 1. 它解决什么问题

加载器（塞进游戏 APK 的 `libmain.so`）需要能**读**到 mod 文件，而这个 App 需要能**写**进去。
听起来简单，但在 Android 11+ 的作用域存储下，这件事几乎没有位置可选：

| 位置 | 管理器能写? | 游戏能读? | 说明 |
|---|---|---|---|
| `/sdcard/Android/data/<游戏>/files/` | ❌ | ✅ | FUSE 按 uid 遮蔽，第三方 App **连 stat 都返回 ENOENT** |
| `/sdcard/Android/media/<管理器包名>/` | ✅ | ❌ | 属主是管理器 uid，游戏进程**没有 `ext_data_rw` 组**，读不了 |
| **`/sdcard/Android/media/<游戏包名>/`** | ✅ | ✅ | **唯一交集** |
| `/sdcard/DeadCellsMods/` | ⚠️ | ⚠️ | 需要 `MANAGE_EXTERNAL_STORAGE`，且部分 ROM 仍受限制 |

选第三个的原因：`Android/media/<包名>/` 的**属主是那个包自己的 uid**，
所以挂在**游戏包名**下时，游戏进程天然有权限读；同时 `Android/media` 对整个系统
是公开的，别的 App 也写得进去。

> **实测细节**：该目录下所有文件在 FUSE 层都被映射成 `media_rw` 属主，
> `chmod` 调用会"成功"但权限位**不变**。所以不要试图靠改权限打通访问。

```
┌─────────────────┐  写 →  ┌──────────────────────────────┐
│  Mod 管理器 App  │        │ /sdcard/Android/media/       │
│  (第三方, 无特权) │        │   com.bilibili.deadcells     │
└─────────────────┘        │   .mobile/DeadCellsMods/     │
                           └──────────────┬───────────────┘
                                          │ 读
                           ┌──────────────▼───────────────┐
                           │  游戏进程 (拥有完整权限)      │
                           │  └─ libmain.so = 加载器       │
                           └──────────────────────────────┘
```

两者**零权限交集**：不需要 `sharedUserId`，不改游戏身份，不碰存档和登录态。

---

## 2. 关于「所有文件访问」权限

App 声明了 `MANAGE_EXTERNAL_STORAGE`。**这是必需的**，原因值得单独记录，
因为它是一个很容易误判的坑：

> 没有该权限时，`Android/media/<包名>/` 目录的
> `exists()` / `isDirectory()` / `canRead()` **全都返回 `true`**，
> 但 `listFiles()` **静默返回空数组** —— 不抛异常、不报错。

也就是说，权限不足的表现**不是报错，而是"列表是空的"**。
新手极容易把它误判成"目录里确实没文件"，然后去查加载器、查路径、查文件系统，
全都查不出来。

因此 App 在启动时会主动检查权限，未授权时显示可点击的引导文案，
点击直达系统授权页，返回后自动刷新（见 `ModRepo.hasAllFilesAccess()`）。

> 注：该判定在 API < 30 一律返回 `true`（那些版本没有这个权限概念）。

---

## 3. 功能

| 功能 | 说明 |
|---|---|
| **列表** | 显示所有 mod：名称、大小、会覆盖哪个内置包、启用状态 |
| **导入** | 走系统文件选择器（SAF），校验 PAK 魔数，拒绝无效文件 |
| **启用/停用** | 改名 `.pak` ↔ `.pak.off`，不删文件 |
| **删除** | 二次确认后删除 |
| **详情** | 完整路径、覆盖目标、行为解释 |
| **加载日志** | 读 `loader.log`，自动判断并给出结论 |

### 启用/停用是怎么实现的

**不改文件内容，只改文件名。**

加载器按 **basename 精确匹配**引擎请求的资源名（如 `res5.pak`）。
把 `res5.pak` 改名成 `res5.pak.off`，加载器就匹配不到它，于是回退到内置资源 ——
效果等同于移除，但文件还在，随时能改回来。

实测两条路径：

```
启用: assets_exists("res5.pak") -> 外部可读, 返回 1
      ★★ 外载命中: res5.pak -> .../res5.pak (idx=5, 2434513 B)

停用: assets_exists("res5.pak") -> 0 (走内置)
```

### 文件名的含义（重要）

文件名**决定它覆盖哪个包**：

- `res5.pak` → 覆盖游戏内置的 `res5.pak`
- `res3.pak` → 覆盖 `res3.pak`
- `测试mod.pak` → **不会生效**，加载器不会把它当作任何资源的替代品

引擎固定请求 `res.pak`、`res1.pak` … `res4.pak`（部分版本还有 `res5`/`res6`）。
编号越大加载越晚，同名资源以晚加载的为准。

App 在导入时会检查文件名是否符合这个模式，不符合就弹窗提醒 ——
这是真实用户最常踩的坑（从别处下载的 mod 名字往往很随意）。

---

## 4. 源码结构

```
dcmodmanager/
├── AndroidManifest.xml
├── build.sh                                    # 无 Gradle 的构建脚本
├── res/
│   ├── values/{strings,colors}.xml
│   ├── layout/{activity_main,activity_log,item_mod}.xml
│   ├── drawable/ic_launcher*.xml               # 矢量图标，无需 PNG
│   └── mipmap-anydpi-v26/ic_launcher.xml
└── src/com/dsharnessmobile/deadcells/modmanager/
    ├── ModRepo.java        # 目录定位 / 枚举 / 增删 / 权限 / 日志
    ├── MainActivity.java   # 主界面：列表、导入、启停、删除
    └── LogActivity.java    # 日志查看与导出
```

设计上的取舍：

- **不用 AndroidX**。本机没有 Gradle，靠 `aapt2 + javac + d8` 手工构建，
  引入任何第三方库都会让构建复杂很多。系统框架 API 足够。
- **权限检查放在 `reload()` 里**，而不是只在 `onCreate`。
  这样用户从系统设置授权后返回，`onResume → reload` 会自动发现权限已给并刷新。
- **`LogActivity` 会给出结论**，而不是只把原始日志丢出来。
  用户不需要知道 `★ 外载命中` 意味着什么。

---

## 5. 如何在设备上构建（没有 Gradle）

`build.sh` 走的是：

```
aapt2 compile → aapt2 link → javac → d8 → zip → apksigner
```

### 前提

| 组件 | 位置 | 说明 |
|---|---|---|
| JDK 21 | `$PREFIX/lib/jvm/java-21-openjdk` | javac / java |
| **aapt2 (arm64)** | `$PREFIX/opt/aapt2x/.../bin/aapt2` | 见下方说明 |
| android.jar | `$SDK/platforms/android-34/android.jar` | API 34 |
| d8 | `$SDK/build-tools/36.0.0/d8` | **必须 36**，见下方说明 |
| apksigner | `$PREFIX/localbin/apksigner` | |

### 两个必须知道的坑

**坑 1：官方 build-tools 里的 `aapt2`/`zipalign` 是 x86-64 的。**

```
error: "/.../aapt2" is for EM_X86_64 (62) instead of EM_AARCH64 (183)
```

它们在 arm64 设备上根本跑不了。解法是用 **Termux 的 arm64 版 aapt2**：

```bash
# 下载 + 手动解包（本机 xz 不可执行，用 Python 解）
python3 -c "
import lzma, tarfile, io
raw = lzma.decompress(open('aapt2.deb','rb').read().split(b'data.tar.xz')[0])
"
```

它依赖 `libfmt/libprotobuf/libabseil/libpng/libzopfli`，需要一并装上。
（`zipalign` 同样是 x86-64，但可以不用 —— apksigner 会处理对齐。）

**坑 2：build-tools 34.0.0 的 d8 **遇到任何匿名内部类就崩溃**。**

```
Error in MainActivity$1.class:
java.lang.NullPointerException: Cannot invoke "String.length()" because "<parameter1>" is null
```

这是 d8 8.2.2 的 bug，最小复现：

```java
public class B {
  interface L { void run(); }
  private int f;
  void go(){ run(new L(){ public void run(){ f=1; } }); }   // ← 只要有匿名类就崩
  void run(L l){ l.run(); }
}
```

与 `--release` 版本无关（17/11/8 都崩），也跟是否捕获外部变量无关。
**解法：装 `build-tools;36.0.0`，用 d8 8.10.9**（已修复）。

### 另一条约束：`javac --release 17`

d8 读不了 Java 21 的 class 文件（major 65）：

```
Unsupported class file major version 65
```

所以 `build.sh` 里用 `--release 17`（major 61）。这个跟上面的坑是**两个独立问题**，
都踩到了才编得出来。

### 构建

```bash
export DC_KEYSTORE=~/dckeys/dc.keystore    # 默认值就是这个
cd dcmodmanager
./build.sh              # 只构建
./build.sh install      # 构建并安装
```

产物：`out/modmanager.apk`（约 25 KB）。

---

## 6. 日志判定逻辑

`LogActivity` 读 `loader.log`（在 mod 目录里）并给出结论：

| 日志特征 | 提示 |
|---|---|
| 含 `★★ 外载命中` | 至少一次外载成功 ✅ |
| 含 `Permission denied` | mod 目录位置不对 |
| 含 `modsrv` + `ENTER` | 加载器已运行，但本次没命中外部 mod |
| 都没有 | 不是加载器版，或还没启动过游戏 |

**为什么日志要落盘而不是只看 logcat**：游戏启动瞬间日志量极大，
logcat 的环形缓冲（本机约 4 MiB）会把早期输出冲掉 ——
`DCMOD` 标记的行会在游戏真正起来之前就被挤出去。所以加载器直接把日志写文件。

---

## 7. 已知限制

- **Mod 目录是硬编码的游戏包名**。换渠道（如从 B 站版换到其他渠道）需要
  同时改 `ModRepo.GAME_PKG` 和加载器里的 `MOD_DIRS`。
- **只支持 .pak 级别的替换**，不做内容级编辑。要改数值需要自己生成 pak
  （见 `PAK格式与改包指南.md`）。
- **不能自动安装加载器版游戏**。App 会给出说明，但安装动作要用户自己做
  （系统不允许普通 App 静默装 APK）。
- 导入是**整文件复制**，几 MB 的 pak 会有短暂耗时（同步执行，未开线程）。
