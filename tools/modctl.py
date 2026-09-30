#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modctl.py — 《重生细胞》外载 Mod 管理工具

⚠ 必须通过 **shell 通道 (uid 2000)** 运行本脚本 —— bash 进程(uid ~3540)
  受作用域存储限制,看不到 /sdcard/Android/data/。在 DSH 里用
  android_shell_exec 调用;或先 `cp` 进 /data/local/tmp 再跑。

用法:
    python3 modctl.py install            # 把 modctl 编译出的 libmain.so 打进 base APK 并签名
    python3 modctl.py push  <pak文件...>  # 把 pak 推进游戏的外载目录(自动按 basename 命名)
    python3 modctl.py list               # 列出当前生效的外部 mod
    python3 modctl.py log                # 查看加载器日志
    python3 modctl.py clear              # 清空外部 mod 目录

关键事实(踩过的坑):
  * 外载目录必须是 App 自己的 external files 目录 —— /sdcard/ 根目录在
    Android 11+ 作用域存储下只能 stat 不能 open。
  * `cmd package clear` 会连 external 目录一起删,所以 push 必须在 clear 之后。
  * 目录需要 setgid(2777),否则游戏进程写的 loader.log shell 读不出来。
"""
import os, shutil, subprocess, sys, time

PKG      = "com.bilibili.deadcells.mobile"
MOD_DIR  = f"/sdcard/Android/data/{PKG}/files/DeadCellsMods"
ROOT     = os.path.dirname(os.path.abspath(__file__))

SHELL = os.environ.get("SHELL") or "/bin/sh"
if not os.path.exists(SHELL):
    SHELL = "/bin/sh"

APKSIGNER = os.environ.get("APKSIGNER", "apksigner")

def sh(cmd, check=False):
    print(f"$ {cmd}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           executable=SHELL)
    except Exception as e:
        print(f"[执行异常] {e}")
        if check: sys.exit(1)
        return ""
    out = (r.stdout or "") + (r.stderr or "")
    if out.strip(): print(out.strip())
    if check and r.returncode != 0:
        sys.exit(f"命令失败(rc={r.returncode})")
    return out

def is_file(p):
    """用 shell 通道判断外部文件是否存在(bash 进程可能看不到 /sdcard/Android/data)"""
    return subprocess.run(["test", "-f", p]).returncode == 0

def cmd_push(args):
    if not args:
        sys.exit("用法: modctl.py push <pak文件...>")
    sh(f"mkdir -p {MOD_DIR}")
    for src in args:
        if not os.path.isfile(src):
            sys.exit(f"找不到文件: {src}")
        name = os.path.basename(src)
        sh(f"cp '{src}' {MOD_DIR}/{name}")
        sh(f"chmod 666 {MOD_DIR}/{name}")
    sh(f"chmod 2777 {MOD_DIR}")          # setgid: 让游戏进程写的文件可被读
    sh(f"ls -la {MOD_DIR}/")

def cmd_list(_):
    print(f"外载目录: {MOD_DIR}\n")
    sh(f"ls -la {MOD_DIR}/ 2>/dev/null || echo '(目录不存在)'")

def cmd_log(_):
    sh(f"cat {MOD_DIR}/loader.log 2>/dev/null | tail -40 || echo '(暂无日志)'")

def cmd_clear(_):
    sh(f"rm -f {MOD_DIR}/*.pak")
    sh(f"ls -la {MOD_DIR}/")

def cmd_install(args):
    """打包含外载加载器的 APK 并安装"""
    base  = args[0] if args else f"{ROOT}/work/base359.apk"
    so    = f"{ROOT}/loader/build/lib/arm64-v8a/libmain.so"
    raw   = "/sdcard/Download/_modctl_raw.apk"
    out   = "/sdcard/Download/DeadCells-外载Mod版.apk"
    ks    = os.environ.get("DC_KEYSTORE", os.path.expanduser("~/.android/debug.keystore"))

    if not os.path.isfile(base): sys.exit(f"找不到 base APK: {base}")
    if not os.path.isfile(so):   sys.exit(f"找不到 loader: {so}")

    sh(f"cd {ROOT} && python3 apkzip.py swap '{base}' '{raw}' '{so}' lib/arm64-v8a/libmain.so", check=True)
    sh(f"{APKSIGNER} sign "
       f"--ks '{ks}' --ks-pass pass:android --key-pass pass:android --ks-key-alias dckey "
       f"--v2-signing-enabled true --v1-signing-enabled false --out '{out}' '{raw}'", check=True)
    sh(f"rm -f {raw}")
    print(f"\n✅ 已生成: {out}")
    print("下一步(在 shell 通道执行):")
    print(f"  cmd package install -r -d {out}")
    print(f"  cmd package clear {PKG}")
    print(f"  python3 {ROOT}/modctl.py push <你的.pak>")

CMDS = {"push": cmd_push, "list": cmd_list, "log": cmd_log,
        "clear": cmd_clear, "install": cmd_install}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    CMDS[sys.argv[1]](sys.argv[2:])
