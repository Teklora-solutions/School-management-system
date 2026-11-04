#!/bin/sh
set -eu

# Runs only when PostgreSQL initializes an EMPTY data directory. Django owns its
# database but cannot create roles/databases or act as a PostgreSQL superuser.
if [ "${APP_DB_USER:?}" = "$POSTGRES_USER" ]; then
    echo "APP_DB_USER must differ from the PostgreSQL administrator" >&2
    exit 1
fi
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 \
    --set=app_user="$APP_DB_USER" \
    --set=app_db="$POSTGRES_DB" <<'SQL'
\getenv app_password APP_DB_PASSWORD
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'app_db', :'app_user') \gexec
SQL
