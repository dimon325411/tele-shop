#!/usr/bin/env bash
# Restore a backup: ./scripts/restore.sh backups/market-20261024-031700.sql.gz
# Stops bot/worker/api first so nothing writes during the restore.
set -euo pipefail
cd "$(dirname "$0")/.."
file="${1:?usage: restore.sh <backup.sql.gz>}"
[ -f "$file" ] || { echo "no such file: $file"; exit 1; }
read -r -p "This REPLACES the current database with $file. Type 'yes' to continue: " answer
[ "$answer" = "yes" ] || { echo "aborted"; exit 1; }
docker compose stop bot worker api
gunzip -c "$file" | docker compose exec -T postgres psql -U market -d market -v ON_ERROR_STOP=1 -q
docker compose start bot worker api
echo "restored from $file"
