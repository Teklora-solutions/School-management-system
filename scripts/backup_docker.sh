#!/usr/bin/env bash
# Consistent PostgreSQL dump through the container; no published DB port needed.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/backups
file="data/backups/school_$(date -u +%Y%m%dT%H%M%SZ)_$$.pg_dump"
trap 'rm -f "$file.partial"' EXIT
docker compose exec -T db \
    sh -c 'exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-password' \
    > "$file.partial"
mv "$file.partial" "$file"
printf 'Database backup written to %s\n' "$file"
# Uploads are separate: snapshot data/media as well, preferably with writes paused.
