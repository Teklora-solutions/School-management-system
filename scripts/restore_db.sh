#!/usr/bin/env bash
# =============================================================================
# scripts/restore_db.sh
# ----------------------
# Restore the school management system database from a backup file.
#
# Usage:
#   ./scripts/restore_db.sh backups/school_db_20260101_020000.pg_dump
#   ./scripts/restore_db.sh backups/school_db_20260101_020000.sqlite3
#
# PostgreSQL restore creates a fresh DB, so run against a non-production
# replica first to validate, then against the real target.
#
# ⚠  WARNING: This DROPS and recreates the target database.
#    Always verify the backup file before running in production.
#
# Tested restore procedure:
#   1. ssh into the server
#   2. Activate the venv:  source /srv/school/.venv/bin/activate
#   3. Stop the app:       sudo systemctl stop school-gunicorn
#   4. Run this script:    ./scripts/restore_db.sh /path/to/backup.pg_dump
#   5. Run migrations:     python backend/manage.py migrate --run-syncdb
#   6. Restart the app:    sudo systemctl start school-gunicorn
#   7. Verify /readyz/:    curl http://localhost:8000/readyz/
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"

# ── Validate argument ──────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <backup_file>" >&2
    echo "Example: $0 backups/school_db_20260101_020000.pg_dump" >&2
    exit 1
fi

BACKUP_FILE="$1"
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "[ERROR] Backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

# Load .env
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "$DATABASE_URL" ]]; then
    DATABASE_URL="sqlite:////$PROJECT_ROOT/backend/db.sqlite3"
fi

# ── Detect backup type from extension ─────────────────────────────────────────
BACKUP_EXT="${BACKUP_FILE##*.}"

# ── PostgreSQL restore ─────────────────────────────────────────────────────────
if [[ "$BACKUP_EXT" == "pg_dump" ]]; then
    if [[ "$DATABASE_URL" != postgres://* ]] && [[ "$DATABASE_URL" != postgresql://* ]]; then
        echo "[ERROR] Backup is PostgreSQL format but DATABASE_URL is not PostgreSQL." >&2
        exit 1
    fi

    DB_USER=$(echo "$DATABASE_URL" | sed -E 's|postgres(ql)?://([^:]+):.*|\2|')
    DB_PASS=$(echo "$DATABASE_URL" | sed -E 's|postgres(ql)?://[^:]+:([^@]+)@.*|\2|')
    DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|postgres(ql)?://[^@]+@([^:/]+).*|\2|')
    DB_PORT=$(echo "$DATABASE_URL" | sed -E 's|.*:([0-9]+)/.*|\1|; s|.*@[^:]+/.*|5432|')
    DB_NAME=$(echo "$DATABASE_URL" | sed -E 's|.*/([^?]+).*|\1|')

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restoring PostgreSQL database '$DB_NAME' from $BACKUP_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠  This will DROP and recreate the database."
    read -r -p "Type 'yes' to confirm: " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Aborted." ; exit 0
    fi

    export PGPASSWORD="$DB_PASS"

    # Terminate existing connections before drop
    psql --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
         --dbname="postgres" --no-password \
         -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB_NAME' AND pid <> pg_backend_pid();" \
         2>/dev/null || true

    # Drop and recreate
    psql --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
         --dbname="postgres" --no-password \
         -c "DROP DATABASE IF EXISTS \"$DB_NAME\";"

    psql --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
         --dbname="postgres" --no-password \
         -c "CREATE DATABASE \"$DB_NAME\";"

    # Restore
    pg_restore \
        --host="$DB_HOST" \
        --port="$DB_PORT" \
        --username="$DB_USER" \
        --dbname="$DB_NAME" \
        --no-password \
        --no-owner \
        --no-acl \
        "$BACKUP_FILE"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] PostgreSQL restore complete."
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Run: python backend/manage.py migrate --run-syncdb"

# ── SQLite restore ─────────────────────────────────────────────────────────────
elif [[ "$BACKUP_EXT" == "sqlite3" ]]; then
    DB_PATH=$(echo "$DATABASE_URL" | sed -E 's|sqlite:////(.*)|\1|; s|sqlite:///(.*)|\1|')

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restoring SQLite database from $BACKUP_FILE → $DB_PATH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠  This will overwrite $DB_PATH"
    read -r -p "Type 'yes' to confirm: " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Aborted." ; exit 0
    fi

    # Take a safety snapshot of current DB before overwriting
    SAFETY_COPY="${DB_PATH}.pre_restore_$(date +%Y%m%d_%H%M%S)"
    if [[ -f "$DB_PATH" ]]; then
        cp "$DB_PATH" "$SAFETY_COPY"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Safety copy saved: $SAFETY_COPY"
    fi

    cp "$BACKUP_FILE" "$DB_PATH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SQLite restore complete."

else
    echo "[ERROR] Unknown backup format: .$BACKUP_EXT" >&2
    echo "Expected .pg_dump (PostgreSQL) or .sqlite3 (SQLite)" >&2
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════════"
echo "  Restore complete. Post-restore checklist:"
echo "  1. python backend/manage.py migrate --check"
echo "  2. curl http://localhost:8000/readyz/"
echo "  3. Log in as admin and verify data integrity"
echo "  4. Restart gunicorn: sudo systemctl restart school-gunicorn"
echo "═══════════════════════════════════════════════"
