#!/usr/bin/env bash
# ============================================================
# 轻量 SLO 告警脚本（C5 P2-4）——零外部依赖，cron 执行
# 设计：直接 curl 抓 /metrics/prometheus，解析 gk_ 指标，超阈值告警
#       不依赖 Prometheus/Grafana（2C/3.6G 资源受限方案）
# 原理：与 metrics.py 的 SLO 阈值一致（SLO_P95_MS=30000, SLO_AVAILABILITY=99.0）
#
# cron 注册（每 5 分钟）（服务器）：
#   crontab -e
#   */5 * * * * cd /home/ubuntu/gongkao && ./scripts/alert.sh >> logs/alert.log 2>&1
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y-%m-%d\ %H:%M:%S)"

# 抓取指标端点（docker 内通过 nginx 3000 反代，宿主机直接 localhost）
METRICS_URL="${METRICS_URL:-http://127.0.0.1:3000/api/metrics/prometheus}"
SLO_WEBHOOK="${SLO_WEBHOOK_URL:-}"  # 可选 webhook（与 metrics.py 共享）

# 阈值（与 metrics.py SLO 模块对齐）
P95_THRESHOLD=30000
AVAIL_THRESHOLD=99.0

# 抓取
DATA="$(curl -s --max-time 10 "$METRICS_URL" || true)"
if [ -z "$DATA" ]; then
    echo "[$TS] ❌ 无法获取 metrics（$METRICS_URL 不可达）"
    exit 1
fi

# 解析关键指标（用 awk 行/列匹配，零依赖）
P95_VAL="$(echo "$DATA" | awk '/^gk_p95_latency_ms /{print $2}')"
AVAIL_VAL="$(echo "$DATA" | awk '/^gk_availability_pct /{print $2}')"
BREACH_VAL="$(echo "$DATA" | awk '/^gk_slo_breach_total /{print $2}')"
ASK_OK="$(echo "$DATA" | awk '/^gk_ask_total /{print $2}')"
ASK_FAIL="$(echo "$DATA" | awk '/^gk_ask_fail_total /{print $2}')"

ALERTS=""

# P95 检查
if [ -n "$P95_VAL" ] && [ "$(echo "$P95_VAL > $P95_THRESHOLD" | bc 2>/dev/null)" = "1" ]; then
    ALERTS="$ALERTS [P95] ${P95_VAL}ms > 阈值 ${P95_THRESHOLD}ms;"
fi

# 可用性检查
if [ -n "$AVAIL_VAL" ] && [ "$(echo "$AVAIL_VAL < $AVAIL_THRESHOLD" | bc 2>/dev/null)" = "1" ]; then
    ALERTS="$ALERTS [可用性] ${AVAIL_VAL}% < 阈值 ${AVAIL_THRESHOLD}%;"
fi

# ask 失败率（最近累计，近似）
if [ -n "$ASK_OK" ] && [ -n "$ASK_FAIL" ] && [ "$ASK_OK" -gt 0 ]; then
    FAIL_RATE="$(echo "scale=2; $ASK_FAIL * 100 / $ASK_OK" | bc 2>/dev/null)"
    if [ -n "$FAIL_RATE" ] && [ "$(echo "$FAIL_RATE > 20" | bc 2>/dev/null)" = "1" ]; then
        ALERTS="$ALERTS [ask失败率] ${FAIL_RATE}% > 20%;"
    fi
fi

if [ -n "$ALERTS" ]; then
    echo "[$TS] ⚠️ SLO 告警: $ALERTS"
    # webhook 推送（配置 SLO_WEBHOOK_URL 后才外发）
    if [ -n "$SLO_WEBHOOK" ]; then
        curl -s --max-time 5 -X POST -H "Content-Type: application/json" \
            -d "{\"text\":\"[$TS] SLO 告警: $ALERTS\",\"event\":\"slo_alert\"}" \
            "$SLO_WEBHOOK" >/dev/null 2>&1 || true
    fi
elif [ -n "$BREACH_VAL" ]; then
    echo "[$TS] ✅ SLO 正常（breach_total=$BREACH_VAL, p95=${P95_VAL:-N/A}ms, avail=${AVAIL_VAL:-N/A}%）"
fi