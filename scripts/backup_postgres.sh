#!/bin/zsh
set -euo pipefail

root="${0:A:h:h}"
backup_dir="$root/data/backups/postgres"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/market-live-$stamp.dump"
PGHOST="$root/data/pg-socket" "$root/data/pg-runtime/bin/pg_dump" -Fc -f "$target" market_live
chmod 600 "$target"
find "$backup_dir" -name 'market-live-*.dump' -type f -mtime +14 -delete
print -- "$target"
