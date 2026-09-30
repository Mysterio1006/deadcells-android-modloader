#!/usr/bin/env bash
#
# build.sh —— 在没有 Gradle 的 Android 设备上手工构建本 App。
#
# 链路:  aapt2 compile -> aapt2 link -> javac -> d8 -> zip -> apksigner
#
# 为什么这么麻烦: 本机没有 gradle/android studio, 且官方 build-tools 里的
# aapt2/zipalign 是 x86-64 的, 跑不了。aapt2 用的是 Termux 的 arm64 版本。
#
# 可覆盖的环境变量:
#   PREFIX         Termux 前缀 (默认自动探测)
#   ANDROID_SDK    SDK 根目录
#   DC_KEYSTORE    签名用 keystore (默认 ~/dckeys/dc.keystore)
#
# 用法:  ./build.sh          # 构建并签名
#        ./build.sh install  # 构建 + 安装到本机
set -e

# Termux 前缀：优先环境变量，其次自动探测
if [ -z "$PREFIX" ]; then
    for c in /data/data/com.termux/files/usr \
             /data/data/com.dsharnessmobile.shell/files/usr; do
        [ -d "$c" ] && PREFIX=$c && break
    done
fi

export JAVA_HOME=${JAVA_HOME:-$PREFIX/lib/jvm/java-21-openjdk}
export PATH=$JAVA_HOME/bin:$PREFIX/bin:$PATH
export LD_LIBRARY_PATH=$PREFIX/lib

SDK=${ANDROID_SDK:-$PREFIX/opt/android-sdk}
# 必须用 build-tools 36 —— 34 的 d8 8.2.2 遇到匿名内部类就崩 (见 docs/Mod管理器.md)
BT=$SDK/build-tools/36.0.0
ANDROID_JAR=$SDK/platforms/android-34/android.jar

# 官方 aapt2 是 x86-64, 必须用 Termux 的 arm64 版
AAPT2=${AAPT2:-$PREFIX/opt/aapt2x/data/data/com.termux/files/usr/bin/aapt2}
APKSIGNER=${APKSIGNER:-$PREFIX/localbin/apksigner}

ROOT=$(cd "$(dirname "$0")" && pwd)
OUT=$ROOT/out
KS=${DC_KEYSTORE:-$HOME/dckeys/dc.keystore}

rm -rf "$OUT"
mkdir -p "$OUT"/{res,gen,cls,dex}

echo "==> [1/6] aapt2 compile 资源"
"$AAPT2" compile --dir "$ROOT/res" -o "$OUT/res.zip"

echo "==> [2/6] aapt2 link (生成 R.java + 未打包 dex 的 base apk)"
"$AAPT2" link \
    -o "$OUT/base.apk" \
    -I "$ANDROID_JAR" \
    --manifest "$ROOT/AndroidManifest.xml" \
    --java "$OUT/gen" \
    --min-sdk-version 24 --target-sdk-version 33 \
    --version-code 1 --version-name 1.0 \
    "$OUT/res.zip"

echo "==> [3/6] javac"
# --release 17: d8 8.2.2 读不了 Java 21 的 class 文件 (major 65), 必须降到 17
find "$ROOT/src" "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac --release 17 -encoding UTF-8 \
      -cp "$ANDROID_JAR" \
      -d "$OUT/cls" \
      -sourcepath "$ROOT/src:$OUT/gen" \
      @"$OUT/sources.txt"

echo "==> [4/6] d8 -> dex"
# 必须用 build-tools 36 —— 34.0.0 自带的 d8 8.2.2 遇到**任何匿名内部类**都会
# 抛 NullPointerException 而崩溃（最小复现已确认），本 App 大量使用匿名
# 监听器，34 完全编不了。d8 8.10.9 已修复。
"$BT/d8" --min-api 24 --lib "$ANDROID_JAR" \
      --output "$OUT/dex" \
      $(find "$OUT/cls" -name '*.class')

echo "==> [5/6] 打包 classes.dex"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
( cd "$OUT/dex" && zip -q -X "$OUT/unsigned.apk" classes.dex )

echo "==> [6/6] 签名"
if [ ! -f "$KS" ]; then
    echo "!! 找不到 keystore: $KS"
    echo "   设置 DC_KEYSTORE 环境变量, 或先用 keytool 生成一个。"
    exit 1
fi
"$APKSIGNER" sign \
    --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --ks-key-alias dckey \
    --v2-signing-enabled true --v1-signing-enabled false \
    --out "$OUT/modmanager.apk" "$OUT/unsigned.apk"

echo
echo "✅ 构建完成: $OUT/modmanager.apk"
ls -la "$OUT/modmanager.apk"

if [ "$1" = "install" ]; then
    echo "==> 安装"
    cp "$OUT/modmanager.apk" /data/local/tmp/mm.apk
    cmd package install -r /data/local/tmp/mm.apk || true
fi
