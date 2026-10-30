#!/usr/bin/env bash
# Database backup: ./scripts/backup.sh  -> backups/market-YYYYmmdd-HHMMSS.sql.gz
# Keeps the newest $KEEP backups (default 14). Run from cron, e.g.: 17 3 * * * cd /opt/telegram-market && ./scripts/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP="${KEEP:-14}"
mkdir -p backups
file="backups/market-$(date -u +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T postgres pg_dump -U market -d market --no-owner --clean --if-exists | gzip -9 > "$file"
gzip -t "$file"
echo "backup written: $file ($(du -h "$file" | cut -f1))"
ls -1t backups/market-*.sql.gz | tail -n +"$((KEEP + 1))" | xargs -r rm --
