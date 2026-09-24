#!/bin/bash
# gongkao 云端启动包装：幂等启动 compose 双容器，前台持有进程。
# 由发布平台拉起，PORT 环境变量（3000）由 frontend 容器端口映射承接。

set -e
cd "$(dirname "$0")"

# docker daemon 兜底（沙箱休眠恢复后可能未自动拉起）
if ! docker info >/dev/null 2>&1; then
  (service docker start || dockerd >/var/log/dockerd-recover.log 2>&1 &)
  for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi

# 幂等启动/保活双容器（镜像已预先 build 好；容器自挂掉由 restart 策略自愈）
docker compose up -d

# 前台跟随容器状态，保持进程存活
docker compose logs -f --tail 5
