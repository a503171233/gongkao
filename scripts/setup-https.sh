#!/usr/bin/env bash
# ============================================================
# HTTPS 一键部署脚本（C5 P1-1）—— 服务器 YOUR_SERVER_IP 上执行
#
# 前置条件：
#   1. 域名已解析到本机公网 IP（A 记录 → YOUR_SERVER_IP）
#   2. 服务器已装 docker compose（当前环境已具备）
#   3. 以 ubuntu 用户执行（docker 免 sudo 已配）
#
# 用法：
#   DOMAIN=gk.example.com ./scripts/setup-https.sh
#
# 步骤：域名校验 → 安装 certbot → 停 3000 释放 80 → 签发证书
#       → 生成 nginx.https.conf + docker-compose.https.yml → 更新 .env 回调
#       → 重建容器 → 冒烟验证（curl -k https://域名/api/health）
# ============================================================
set -euo pipefail

DOMAIN="${DOMAIN:?用法: DOMAIN=你的域名 ./scripts/setup-https.sh}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> [1/8] 校验域名解析: $DOMAIN"
SERVER_IP="$(curl -s --max-time 5 ifconfig.me || echo '')"
DOMAIN_IP="$(dig +short "$DOMAIN" 2>/dev/null | head -1 || nslookup "$DOMAIN" 2>/dev/null | awk '/^Address: /{print $2}' | head -1 || echo '')"
if [ -z "$DOMAIN_IP" ]; then
    echo "❌ 域名未解析，请先在 DNS 控制台把 $DOMAIN 的 A 记录指向服务器公网 IP"
    exit 1
fi
if [ -n "$SERVER_IP" ] && [ "$DOMAIN_IP" != "$SERVER_IP" ]; then
    echo "⚠️  域名解析到 $DOMAIN_IP，但本机出口 IP 是 $SERVER_IP（可能 NAT，继续）"
fi
echo "    域名解析 OK: $DOMAIN → $DOMAIN_IP"

echo "==> [2/8] 安装 certbot"
if ! command -v certbot >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq certbot
fi

echo "==> [3/8] 停 frontend（释放 80 端口给 standalone 签证书）"
docker compose stop frontend || true

echo "==> [4/8] certbot 签发证书（standalone 模式，80 端口）"
sudo certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email --keep-until-expiring || true

echo "==> [5/8] 生成 nginx.https.conf 与 docker-compose.https.yml"
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "❌ 证书未生成: $CERT_DIR 不存在，请检查上一步 certbot 输出"
    exit 1
fi
sed "s/__DOMAIN__/$DOMAIN/g" frontend/nginx.https.conf.example > frontend/nginx.https.conf
sed "s/__DOMAIN__/$DOMAIN/g" docker-compose.https.yml.example > docker-compose.https.yml
echo "    已生成 frontend/nginx.https.conf 与 docker-compose.https.yml"

echo "==> [6/8] 更新 .env 的 GITEE_REDIRECT_URI 为 https"
if [ -f .env ]; then
    sed -i "s|^GITEE_REDIRECT_URI=.*|GITEE_REDIRECT_URI=https://$DOMAIN/api/auth/gitee/callback|" .env \
        || true
    # 若 .env 无该行则追加
    grep -q "^GITEE_REDIRECT_URI=" .env || \
        echo "GITEE_REDIRECT_URI=https://$DOMAIN/api/auth/gitee/callback" >> .env
    echo "    .env 已更新（回调 https://$DOMAIN/api/auth/gitee/callback）"
else
    echo "    ⚠️ 未找到 .env，请手动把 GITEE_REDIRECT_URI 改为 https://$DOMAIN/api/auth/gitee/callback"
fi

echo "==> [7/8] 重建 frontend 容器（HTTPS override）"
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d frontend

echo "==> [8/8] 冒烟验证"
sleep 3
echo "--- HTTP→HTTPS 跳转 ---"
curl -s -o /dev/null -w "http 状态码: %{http_code} → %{redirect_url}\n" "http://$DOMAIN/api/health" || true
echo "--- HTTPS 直连 ---"
curl -sk -w "\nhttps 状态码: %{http_code}\n" "https://$DOMAIN/api/health" || true
echo ""
echo "✅ HTTPS 部署完成！"
echo "   ⚠️ 若 Gitee 后台回调地址仍为 http，请同步改为 https://$DOMAIN/api/auth/gitee/callback"
echo "   ⚠️ 证书 90 天到期，建议加 cron: sudo crontab -e 添加"
echo "      0 3 * * 1 sudo certbot renew --quiet --deploy-hook 'cd /home/ubuntu/gongkao && docker compose -f docker-compose.yml -f docker-compose.https.yml up -d frontend'"
