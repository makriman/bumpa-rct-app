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

(
  cd "$BACKUP_PATH"
  sha256sum -c SHA256SUMS
)

pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error --dbname "${PGDATABASE}" "$BACKUP_PATH/postgres.dump"

if [[ -f "$BACKUP_PATH/exports.tar.gz" && -d /source/exports ]]; then
  find /source/exports -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -C /source/exports -xzf "$BACKUP_PATH/exports.tar.gz"
fi

if [[ -f "$BACKUP_PATH/hermes.tar.gz" && -d /source/hermes ]]; then
  find /source/hermes -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -C /source/hermes -xzf "$BACKUP_PATH/hermes.tar.gz"
fi

echo "Restore completed from $BACKUP_PATH"
