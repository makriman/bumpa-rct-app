#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${RESTORE_CONFIRM:-}" != "restore-bumpabestie" ]]; then
  echo "Set RESTORE_CONFIRM=restore-bumpabestie to authorize destructive restore" >&2
  exit 2
fi
if [[ -z "${BACKUP_PATH:-}" || ! -d "$BACKUP_PATH" ]]; then
  echo "BACKUP_PATH must name a mounted backup directory" >&2
  exit 2
fi
if [[ ! -f "$BACKUP_PATH/postgres.dump" || ! -f "$BACKUP_PATH/SHA256SUMS" ]]; then
  echo "Backup is incomplete" >&2
  exit 2
fi
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${APP_POSTGRES_PASSWORD:?APP_POSTGRES_PASSWORD is required}"

(
  cd "$BACKUP_PATH"
  sha256sum -c SHA256SUMS
)

unexpected_schemas="$(
  psql -X --tuples-only --no-align --set ON_ERROR_STOP=1 --command \
    "SELECT nspname FROM pg_namespace
     WHERE nspname !~ '^pg_'
       AND nspname NOT IN ('information_schema', 'public')
     ORDER BY nspname"
)"
if [[ -n "$unexpected_schemas" ]]; then
  echo "Restore requires manual review because non-public user schemas exist" >&2
  exit 2
fi

unexpected_extensions="$(
  psql -X --tuples-only --no-align --set ON_ERROR_STOP=1 --command \
    "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname"
)"
if [[ -n "$unexpected_extensions" ]]; then
  echo "Restore requires manual review because non-core extensions exist" >&2
  exit 2
fi

psql -X --set ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DROP SCHEMA public CASCADE;
CREATE SCHEMA public AUTHORIZATION pg_database_owner;
COMMIT;
SQL

/docker-entrypoint-initdb.d/10-app-role.sh
pg_restore --no-owner --no-privileges --exit-on-error --dbname "${PGDATABASE}" "$BACKUP_PATH/postgres.dump"
/docker-entrypoint-initdb.d/10-app-role.sh

if [[ -f "$BACKUP_PATH/exports.tar.gz" && -d /source/exports ]]; then
  find /source/exports -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -C /source/exports -xzf "$BACKUP_PATH/exports.tar.gz"
fi

if [[ -f "$BACKUP_PATH/hermes.tar.gz" && -d /source/hermes ]]; then
  find /source/hermes -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -C /source/hermes -xzf "$BACKUP_PATH/hermes.tar.gz"
fi

echo "Restore completed from $BACKUP_PATH"
