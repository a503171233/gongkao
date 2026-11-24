#!/usr/bin/env bash
# 下载页生成脚本（批次I固化）：把最新 APK 内嵌进 apk.html 模板，产出自包含下载页。
# 用法: ./tools/build-download-page.sh <apk路径> <版本名> [输出html路径]
#   例: ./tools/build-download-page.sh /workspace/apk-dist/gongkao.apk 2.8.1 /tmp/apk.html
# 模板: tools/apk-page-template.html（占位符 __B64__ / __VN__）
# 产出: 单文件下载页（内嵌 base64 整包，微信/QQ 内自动提示用浏览器打开）
#       发版后请与 /gongkao.apk 直链、app-update.json.sha256 三方校验一致。
set -e

APK="${1:?用法: build-download-page.sh <apk路径> <版本名> [输出html]}"
VN="${2:?缺少版本名，如 2.8.1}"
OUT="${3:-/tmp/apk.html}"
TPL="$(cd "$(dirname "$0")" && pwd)/apk-page-template.html"

[ -f "$APK" ] || { echo "!! APK 不存在: $APK"; exit 1; }
[ -f "$TPL" ] || { echo "!! 模板不存在: $TPL"; exit 1; }

python3 - "$APK" "$VN" "$TPL" "$OUT" <<'PY'
import base64, sys, hashlib

apk, vn, tpl, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
t = open(tpl, encoding='utf-8').read()
b64 = base64.b64encode(open(apk, 'rb').read()).decode()
h = t.replace('__B64__', b64).replace('__VN__', vn)
open(out, 'w', encoding='utf-8').write(h)
sha = hashlib.sha256(open(apk, 'rb').read()).hexdigest()
print(f"== 下载页 OK: {out} (v{vn}) ==")
print(f"   内嵌包 sha256: {sha}")
PY
