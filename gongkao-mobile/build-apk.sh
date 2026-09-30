#!/usr/bin/env bash
# 公考学习 App 本地打包脚本（无 Gradle 手工链，沙箱/CI 均可跑）
# 用法: ./build-apk.sh [版本号 版本名 输出路径]
#   默认: 4 / 2.0.1 / /workspace/apk-dist/gongkao.apk
# 依赖: build-tools(aapt2/d8/zipalign/apksigner) + platform android.jar + JDK 8+
set -e

SDK="${SDK_DIR:-/opt/android/android-15}"          # build-tools 目录
AJ="${ANDROID_JAR:-/opt/android/android-35/android.jar}"  # platform android.jar
SRC="$(cd "$(dirname "$0")" && pwd)/app-android/app/src/main"
KS="$(cd "$(dirname "$0")" && pwd)/gongkao-release.keystore"

VC="${1:-4}"
VN="${2:-2.0.1}"
OUT="${3:-/workspace/apk-dist/gongkao.apk}"

B="${BUILD_DIR:-/tmp/gk-apk-build}"
rm -rf "$B" && mkdir -p "$B/gen" "$B/classes" "$B/dex"

echo "[1/6] aapt2 compile 资源…"
"$SDK/aapt2" compile --dir "$SRC/res" -o "$B/res.zip"

echo "[2/6] aapt2 link 生成 R.java 与基础包…"
"$SDK/aapt2" link -o "$B/unsigned.apk" -I "$AJ" --manifest "$SRC/AndroidManifest.xml" \
  --java "$B/gen" --min-sdk-version 24 --target-sdk-version 35 \
  --version-code "$VC" --version-name "$VN" "$B/res.zip"

echo "[3/6] javac 编译源码…"
mkdir -p "$B/classes"
if ! javac --release 8 -classpath "$AJ" -d "$B/classes" \
    "$B/gen/com/gongkao/app/R.java" "$SRC"/java/com/gongkao/app/*.java; then
  echo "!! javac 编译失败"; exit 1
fi

echo "[4/6] d8 转 dex…"
"$SDK/d8" --release --lib "$AJ" --output "$B/dex" $(find "$B/classes" -name "*.class")
(cd "$B/dex" && zip -q -j "$B/unsigned.apk" classes.dex)

echo "[5/6] zipalign 对齐…"
"$SDK/zipalign" -f 4 "$B/unsigned.apk" "$B/aligned.apk"

echo "[6/6] apksigner 签名…"
"$SDK/apksigner" sign --ks "$KS" --ks-key-alias gongkao \
  --ks-pass pass:gongkao2026 --out "$OUT" "$B/aligned.apk"

"$SDK/apksigner" verify "$OUT" && echo "== OK: $OUT (v$VN, versionCode $VC) =="
