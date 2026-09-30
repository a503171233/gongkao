#!/usr/bin/env bash
# 热更补丁编译脚本（批次H）：把补丁源码目录编译为可下发的 patch.dex
# 用法: ./tools/build-patch.sh <补丁源码目录> <输出 dex 路径> [宿主classes目录]
#   例: ./tools/build-patch.sh tools/patch-demo /tmp/patch-demo.dex
# 依赖: android.jar + d8（与 build-apk.sh 同一套 SDK）
# 前提: 宿主 classes 目录（build-apk.sh 的中间产物，默认 /tmp/gk-apk-build/classes）
#       —— 补丁引用的 PatchMain/PatchHooks 接口必须来自宿主 classes，保证类型一致。
set -e

SDK="${SDK_DIR:-/opt/android/android-15}"
AJ="${ANDROID_JAR:-/opt/android/android-35/android.jar}"
HOST_CLASSES="${3:-${BUILD_DIR:-/tmp/gk-apk-build}/classes}"

SRC_DIR="${1:?用法: build-patch.sh <补丁源码目录> <输出.dex> [宿主classes]}"
OUT="${2:?缺少输出路径}"

if [ ! -d "$HOST_CLASSES" ]; then
  echo "!! 宿主 classes 不存在: $HOST_CLASSES（先跑一次 ./build-apk.sh，或显式传第3参）"
  exit 1
fi

B="$(mktemp -d)"
echo "[1/2] javac 编译补丁（classpath = android.jar + 宿主 classes）…"
javac --release 8 -classpath "$AJ:$HOST_CLASSES" -d "$B" "$SRC_DIR"/*.java

echo "[2/2] d8 转 dex → $OUT"
# 宿主 classes 打 jar 传给 d8（接口 desugaring 需要类型信息，消除 not found 警告）
jar cf "$B/host.jar" -C "$HOST_CLASSES" .
"$SDK/d8" --release --lib "$AJ" --lib "$B/host.jar" --output "$(dirname "$OUT")" \
  $(find "$B" -name "*.class")
# d8 输出为 <dirname>/classes.dex，移动到目标名
mv "$(dirname "$OUT")/classes.dex" "$OUT"

echo "== 补丁 OK: $OUT =="
echo "下发哈希: $(sha256sum "$OUT" | cut -c1-64)"
