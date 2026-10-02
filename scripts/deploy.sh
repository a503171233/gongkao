#!/usr/bin/env bash
# ============================================================
# 多老师 RAG 平台 · 一键部署 + 冒烟测试（#28 P1-3）
# 用途：构建 Docker 镜像 → 启动/重载服务 → 冒烟验证（health / ask 联通）
#
# 用法（项目根目录 /home/ubuntu/gongkao）：
#   ./scripts/deploy.sh                  # 构建 + 启动 + 冒烟
#   ./scripts/deploy.sh --no-smoke       # 构建 + 启动（跳过冒烟）
#   ./scripts/deploy.sh --rebuild-only   # 仅构建（不重启）
#   ./scripts/deploy.sh --smoke-only     # 仅冒烟（服务已运行）
#
# 前置条件：
#   - .env 已配置（API_KEY / ADMIN_SECRET 等必填项）
#   - Docker daemon 运行中
#   - docker-compose 已安装
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="http://localhost:3000"       # 前端反向代理接入（nginx → backend）
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-15}"
MODE="full"

for arg in "$@"; do
    case "$arg" in
        --no-smoke)   MODE="no_smoke" ;;
        --rebuild-only) MODE="rebuild" ;;
        --smoke-only) MODE="smoke" ;;
    esac
done

red(){  echo -e "\033[31m$1\033[0m"; }
green(){ echo -e "\033[32m$1\033[0m"; }
yellow(){ echo -e "\033[33m$1\033[0m"; }

# ---- 0. 前置检查 ----
cd "$PROJECT_DIR"

if [ "$MODE" != "smoke" ]; then
    if [ ! -f .env ]; then
        red "❌ 缺少 .env 文件（至少需 API_KEY / ADMIN_SECRET）"; exit 1
    fi
    if ! command -v docker >/dev/null 2>&1; then
        red "❌ docker 未安装"; exit 1
    fi
    if ! command -v docker compose >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
        red "❌ docker-compose 未安装"; exit 1
    fi
fi

COMPOSE_CMD="docker compose"
if ! command -v docker >/dev/null 2>&1; then true; elif ! docker compose version &>/dev/null; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    fi
fi

# ---- 1. 构建镜像 ----
if [ "$MODE" != "smoke" ]; then
    green "[deploy] 构建镜像…"
    $COMPOSE_CMD build --pull 2>&1 || { red "❌ 镜像构建失败"; exit 1; }
    green "[deploy] 镜像构建完成"
fi

# ---- 2. 重建/启动服务 ----
if [ "$MODE" = "no_smoke" ] || [ "$MODE" = "full" ]; then
    green "[deploy] 启动/重载服务…"
    $COMPOSE_CMD up -d --remove-orphans 2>&1 || { red "❌ 服务启动失败"; exit 1; }
    green "[deploy] 服务已启动"

    # 等待后端就绪（最长 30s）
    echo -n "[deploy] 等待后端就绪"
    for i in $(seq 1 30); do
        if curl -sf "$BACKEND_HOST/api/health" >/dev/null 2>&1; then
            echo " ✓"; break
        fi
        echo -n "."; sleep 1
    done
    if ! curl -sf "$BACKEND_HOST/api/health" >/dev/null 2>&1; then
        red "❌ 后端未在 30s 内就绪"; exit 1
    fi
fi

if [ "$MODE" = "rebuild" ]; then
    green "[deploy] 构建完成（未重启服务，请手动 docker compose up -d）"; exit 0
fi

# ---- 3. 冒烟测试 ----
if [ "$MODE" = "smoke" ] || [ "$MODE" = "full" ]; then
    green "[smoke] 开始冒烟…"
    FAIL=0

    # 3a. 健康检查（json 且含 ts 字段）
    HEALTH=$(curl -sf --max-time "$SMOKE_TIMEOUT" "$BACKEND_HOST/api/health" 2>&1) || true
    if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'ts' in d" 2>/dev/null; then
        green "[smoke] ✅ GET /api/health"
    else
        red "[smoke] ❌ /api/health 不可用: ${HEALTH:0:120}"; FAIL=1
    fi

    # 3b. 管理员登录 → 取 token
    ADMIN_USER="${ADMIN_USERNAME:-admin}"
    ADMIN_PASS="${ADMIN_PASSWORD:-}"
    if [ -z "$ADMIN_PASS" ]; then
        yellow "[smoke] ⚠  ADMIN_PASSWORD 未设，跳过 /auth/login 冒烟"
    else
        LOGIN_RESP=$(curl -sf --max-time "$SMOKE_TIMEOUT" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" \
            "$BACKEND_HOST/api/auth/login" 2>&1) || true
        ADMIN_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
        if [ -n "$ADMIN_TOKEN" ]; then
            green "[smoke] ✅ POST /auth/login → token ${ADMIN_TOKEN:0:12}..."
        else
            red "[smoke] ❌ /auth/login 失败: ${LOGIN_RESP:0:120}"; FAIL=1
        fi
    fi

    # 3c. 匿名非流式问答（最低连通验证，用 mock科目 T001）
    if [ -n "${ADMIN_TOKEN:-}" ]; then
        ASK_RESP=$(curl -sf --max-time "$((SMOKE_TIMEOUT * 2))" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $ADMIN_TOKEN" \
            -d '{"teacher_id":"T001","query":"1+1=?（冒烟测试，忽略）","stream":false}' \
            "$BACKEND_HOST/api/ask" 2>&1) || true
        ANSWER=$(echo "$ASK_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('answer','')[:60])" 2>/dev/null || true)
        if [ -n "$ANSWER" ]; then
            green "[smoke] ✅ POST /ask (非流式) → $ANSWER"
        else
            yellow "[smoke] ⚠  /ask 返回空（环境可能无 API_KEY）: ${ASK_RESP:0:120}"
        fi
    else
        yellow "[smoke] ⚠  无 token，跳过 /ask 冒烟"
    fi

    # 3d. Docker 容器运行态检查
    RUNNING=$(docker ps --filter "name=gongkao" --format "{{.Names}}" 2>/dev/null | wc -l || echo 0)
    if [ "$RUNNING" -ge 2 ]; then
        green "[smoke] ✅ docker 容器 running=$RUNNING"
    else
        red "[smoke] ❌ docker 容器不足: running=$RUNNING (预期≥2)"; FAIL=1
    fi

    # 3e. 知识库文件捡查（KB 目录非空）
    KB_COUNT=$(python3 -c "
import os,sys
data=os.path.join(os.environ.get('DATA_DIR',''),'vector') or '/tmp'
kb=os.path.join(data,'kb')
c=len([f for f in os.listdir(kb) if f.endswith('.json')]) if os.path.isdir(kb) else 0
print(c)
" 2>/dev/null || echo 0)
    if [ "$KB_COUNT" -gt 0 ]; then
        green "[smoke] ✅ 知识库文件数=$KB_COUNT"
    else
        yellow "[smoke] ⚠  知识库为空（需在管理页导入文档）"
    fi

    if [ "$FAIL" -eq 0 ]; then
        green "[smoke] ✅ 冒烟全部通过"; exit 0
    else
        red "[smoke] ❌ 冒烟失败（见上方 FAIL 项）"; exit 1
    fi
fi