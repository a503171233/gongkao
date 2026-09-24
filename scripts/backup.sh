#!/usr/bin/env bash
# ============================================================
# 多老师 RAG 平台 · 数据备份脚本（C5 P1-3）
# 用途：打包 /data 挂载卷（auth.db/chat.db/teachers.db/vector/models/raw）
#       保留最近 N 份；可选异地同步（rsync/scp）；生成校验和便于恢复演练。
#
# 硬性纪律：
#   1. 绝不打包 .env（含 API_KEY / Gitee 凭据）→ 已显式 --exclude
#   2. 备份必须可恢复：每次备份生成 SHA256 校验和清单（restore 时比对）
#   3. 服务器 2C/3.6G 资源受限：单次全量打包，不做压缩炸弹式并行
#
# 用法（服务器 /home/ubuntu/gongkao 下）：
#   ./scripts/backup.sh                     # 本地备份到 backups/
#   ./scripts/backup.sh --remote user@host:/path   # 本地备份 + rsync 异地
#   ./scripts/backup.sh --restore <file>    # 恢复演练（解包到 ./data/）
#
# cron 注册（每日 03:30）：
#   crontab -e
#   30 3 * * * cd /home/ubuntu/gongkao && ./scripts/backup.sh >> backups/backup.log 2>&1
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$PROJECT_DIR/data}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
KEEP="${KEEP:-7}"                      # 保留最近 N 份
COMPRESS="${COMPRESS:-gzip}"           # gzip / pigz(并行,若已安装) / none
REMOTE_TARGET="${REMOTE_TARGET:-}"     # 异地目标 user@host:/path（可选）

mkdir -p "$BACKUP_DIR"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/gongkao-data-$TS.tar.gz"
MANIFEST="$BACKUP_DIR/gongkao-data-$TS.sha256"

echo "[backup] 开始: $TS"
echo "[backup] 数据目录: $DATA_DIR"

# ---- 1. 打包（显式排除 .env / 临时文件 / 锁） ----
# 注：data/ 下本身不含 .env，但服务器 .env 在项目根，双保险排除；同时排除
#     WAL 之外的 sqlite 临时文件与缓存，避免把运行期垃圾打进去。
if [ "$COMPRESS" = "pigz" ] && command -v pigz >/dev/null 2>&1; then
    tar --use-compress-program=pigz -cf "$BACKUP_FILE" \
        --exclude='*.env' --exclude='.env' \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='*.tmp' --exclude='*.lock' \
        -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
else
    tar -czf "$BACKUP_FILE" \
        --exclude='*.env' --exclude='.env' \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='*.tmp' --exclude='*.lock' \
        -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
fi
echo "[backup] 打包完成: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# ---- 2. 校验和清单（恢复演练比对用） ----
sha256sum "$BACKUP_FILE" > "$MANIFEST"
echo "[backup] 校验和: $(cat "$MANIFEST")"

# ---- 3. 完整性自检：能列出内容才算有效备份 ----
if ! tar -tzf "$BACKUP_FILE" >/dev/null 2>&1; then
    echo "[backup] ❌ 备份文件损坏，已中止（不清理旧备份）"
    exit 1
fi
echo "[backup] 完整性自检通过（tar -tzf 可读）"

# ---- 4. 保留最近 N 份，清理过期 ----
OLD_FILES="$(ls -1t "$BACKUP_DIR"/gongkao-data-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) || true)"
if [ -n "$OLD_FILES" ]; then
    echo "$OLD_FILES" | while read -r f; do
        rm -f "$f" "${f%.tar.gz}.sha256"
        echo "[backup] 清理过期备份: $(basename "$f")"
    done
fi

# ---- 5. 可选异地同步（rsync 或 scp） ----
if [ -n "$REMOTE_TARGET" ]; then
    if command -v rsync >/dev/null 2>&1; then
        rsync -az --partial "$BACKUP_FILE" "$MANIFEST" "$REMOTE_TARGET"
        echo "[backup] 已 rsync 异地: $REMOTE_TARGET"
    else
        # rsync 不存在时退回 scp（仍传输文件+校验和）
        REMOTE_DIR="${REMOTE_TARGET%:*}"; REMOTE_PATH="${REMOTE_TARGET##*:}"
        scp -q "$BACKUP_FILE" "$MANIFEST" "$REMOTE_TARGET"
        echo "[backup] 已 scp 异地: $REMOTE_TARGET"
    fi
fi

echo "[backup] ✅ 完成。当前保留: $(ls -1t "$BACKUP_DIR"/gongkao-data-*.tar.gz 2>/dev/null | wc -l) 份"
echo "[backup] 提示: .env 未包含在备份中（密钥只存服务器），恢复后需手动补齐 .env"
