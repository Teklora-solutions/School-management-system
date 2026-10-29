#!/usr/bin/env bash
# =============================================================================
# scripts/backup_db.sh
# ---------------------
# Back up the school management system database.
#
# Supports both PostgreSQL (production) and SQLite (development).
# The backup file is written to BACKUP_DIR (default: ./backups/).
#
# Usage:
#   ./scripts/backup_db.sh                    # uses .env for DATABASE_URL
#   DATABASE_URL=postgres://... ./scripts/backup_db.sh
#   BACKUP_DIR=/mnt/backups ./scripts/backup_db.sh
#
# Cron example (daily at 02:00, keep 30 days):
#   0 2 * * * /srv/school/scripts/backup_db.sh >> /var/log/school_backup.log 2>&1
#
# Dependencies: pg_dump (PostgreSQL), sqlite3 (SQLite)
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
KEEP_DAYS="${KEEP_DAYS:-30}"   # delete backups older than this many days
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# Load .env if present (won't override already-exported vars)
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "$DATABASE_URL" ]]; then
    # Fallback: try the Django default SQLite location
    DATABASE_URL="sqlite:////$PROJECT_ROOT/backend/db.sqlite3"
fi

mkdir -p "$BACKUP_DIR"

# ── Determine DB type ─────────────────────────────────────────────────────────
if [[ "$DATABASE_URL" == postgres://* ]] || [[ "$DATABASE_URL" == postgresql://* ]]; then
    DB_TYPE="postgres"
elif [[ "$DATABASE_URL" == sqlite://* ]]; then
    DB_TYPE="sqlite"
else
    echo "[ERROR] Unsupported DATABASE_URL scheme: $DATABASE_URL" >&2
    exit 1
fi

# ── PostgreSQL backup ──────────────────────────────────────────────────────────
if [[ "$DB_TYPE" == "postgres" ]]; then
    # Parse components from DATABASE_URL
    # Expected format: postgres://USER:PASSWORD@HOST:PORT/DBNAME
    DB_USER=$(echo "$DATABASE_URL" | sed -E 's|postgres(ql)?://([^:]+):.*|\2|')
    DB_PASS=$(echo "$DATABASE_URL" | sed -E 's|postgres(ql)?://[^:]+:([^@]+)@.*|\2|')
    DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|postgres(ql)?://[^@]+@([^:/]+).*|\2|')
    DB_PORT=$(echo "$DATABASE_URL" | sed -E 's|.*:([0-9]+)/.*|\1|; s|.*@[^:]+/.*|5432|')
    DB_NAME=$(echo "$DATABASE_URL" | sed -E 's|.*/([^?]+).*|\1|')

    BACKUP_FILE="$BACKUP_DIR/school_db_${TIMESTAMP}.pg_dump"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting PostgreSQL backup → $BACKUP_FILE"
    PGPASSWORD="$DB_PASS" pg_dump \
        --host="$DB_HOST" \
        --port="$DB_PORT" \
        --username="$DB_USER" \
        --format=custom \
        --compress=9 \
        --no-password \
        "$DB_NAME" > "$BACKUP_FILE"

    BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] PostgreSQL backup complete. Size: $BACKUP_SIZE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] File: $BACKUP_FILE"

# ── SQLite backup ──────────────────────────────────────────────────────────────
elif [[ "$DB_TYPE" == "sqlite" ]]; then
    DB_PATH=$(echo "$DATABASE_URL" | sed -E 's|sqlite:////(.*)|\1|; s|sqlite:///(.*)|\1|')
    if [[ ! -f "$DB_PATH" ]]; then
        echo "[ERROR] SQLite database not found: $DB_PATH" >&2
        exit 1
    fi

    BACKUP_FILE="$BACKUP_DIR/school_db_${TIMESTAMP}.sqlite3"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting SQLite backup → $BACKUP_FILE"
    # Use SQLite's online backup API via .backup command for a consistent snapshot
    sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

    BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SQLite backup complete. Size: $BACKUP_SIZE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] File: $BACKUP_FILE"
fi

# ── Prune old backups ─────────────────────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pruning backups older than ${KEEP_DAYS} days..."
find "$BACKUP_DIR" -maxdepth 1 \( -name "*.pg_dump" -o -name "*.sqlite3" \) \
    -mtime +"$KEEP_DAYS" -print -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup complete."
