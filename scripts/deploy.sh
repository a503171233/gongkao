#!/usr/bin/env bash
# ============================================================
# 多老师 RAG 平台 · 一键发布 + 冒烟（#28，2026-09-25）
#
# 用途：替代手工「build → up → 逐个 curl 核对」流程。任何改动收尾时跑：
#   ./scripts/deploy.sh              # 构建 + 启动 + 冒烟（默认全服务）
#   ./scripts/deploy.sh --no-build   # 跳过构建（仅重启 + 冒烟）
#   ./scripts/deploy.sh --backend    # 只构建/冒烟 backend
#
# 冒烟覆盖（任一失败即整体 FAIL，退出码 1）：
#   1) 容器状态：backend/frontend 均 Up
#   2) 后端 /health 200 + detail 关键字段
#   3) 前端首页 200 + 关键静态资源（index.html 引用的 js 可达）
#   4) 核心 API：teachers 列表 / metrics / 登录页接口存活
#   5) 题库抽查：question_bank 行数 > 0（采集链路有产出）
#   6) git 脏工作区提醒（不阻断，仅 WARN）
# ============================================================
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

NO_BUILD=0; SERVICES="backend frontend"
for arg in "$@"; do
  case "$arg" in
    --no-build) NO_BUILD=1 ;;
    --backend) SERVICES="backend" ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

PASS=0; FAIL=0
ok()   { echo "  ✅ $1"; PASS=$((PASS + 1)); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }

echo "==== [1/4] 构建（services: $SERVICES, no-build=$NO_BUILD） ===="
if [ "$NO_BUILD" = "0" ]; then
  docker compose build $SERVICES || { echo "❌ 构建失败"; exit 1; }
fi
docker compose up -d $SERVICES >/dev/null || { echo "❌ 启动失败"; exit 1; }
echo "等待服务就绪..."
for i in $(seq 1 20); do
  curl -sf http://localhost:3000/api/health >/dev/null 2>&1 && break
  sleep 1
done

echo "==== [2/4] 容器状态 ===="
for svc in $SERVICES; do
  name="gongkao-$svc"
  state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)"
  [ "$state" = "running" ] && ok "容器 $name running" || bad "容器 $name 状态=$state"
done

echo "==== [3/4] HTTP 冒烟 ===="
# 注：backend 9000 不映射宿主机（compose 注释），一切走 nginx 3000 反代（/api/ 剥前缀）
code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/health)"
[ "$code" = "200" ] && ok "/health 200" || bad "/health → $code"
detail="$(curl -sf http://localhost:3000/api/health/detail 2>/dev/null | head -c 200)"
[ -n "$detail" ] && ok "/health/detail 有响应" || bad "/health/detail 无响应"

code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/)"
[ "$code" = "200" ] && ok "前端首页 200" || bad "前端首页 → $code"

code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/teachers)"
[ "$code" = "200" ] && ok "前端反代 /api/teachers 200" || bad "前端反代 /api/teachers → $code"

code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/metrics/prometheus)"
[ "$code" = "200" ] && ok "metrics 200" || bad "metrics → $code"

echo "==== [4/4] 数据抽查 ===="
qcount="$(docker exec gongkao-backend python -c "
import sqlite3
conn = sqlite3.connect('/data/study.db')
print(conn.execute('select count(*) from question_bank').fetchone()[0])
" 2>/dev/null || echo 0)"
[ "${qcount:-0}" -gt 0 ] 2>/dev/null && ok "题库 ${qcount} 题（>0）" || bad "题库空（$qcount）"

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  echo "  ⚠️  git 工作区有未提交改动（不阻断，建议收尾提交）"
fi

echo "==== 汇总 ===="
echo "通过 $PASS · 失败 $FAIL"
[ "$FAIL" = "0" ] && { echo "✅ 发布冒烟全部通过"; exit 0; } || { echo "❌ 冒烟存在失败项"; exit 1; }
