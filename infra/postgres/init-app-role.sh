#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${APP_POSTGRES_PASSWORD:?APP_POSTGRES_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$APP_POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE bumpabestie_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bumpabestie_app')
\gexec

ALTER ROLE bumpabestie_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
SELECT format('GRANT CONNECT ON DATABASE %I TO bumpabestie_app', current_database())
\gexec
GRANT USAGE ON SCHEMA public TO bumpabestie_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bumpabestie_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO bumpabestie_app;
SQL
