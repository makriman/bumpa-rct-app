#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

backup_root="${BACKUP_DIR:-/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$backup_root/$timestamp"

if ! [[ "$retention_days" =~ ^[0-9]+$ ]] || ((retention_days < 1)); then
  echo "BACKUP_RETENTION_DAYS must be a positive integer" >&2
  exit 2
fi

mkdir -p "$destination"
pg_dump --format=custom --compress=9 --no-owner --no-privileges --file "$destination/postgres.dump"

if [[ -d /source/exports ]]; then
  tar -C /source/exports -czf "$destination/exports.tar.gz" .
fi

if [[ -d /source/hermes ]]; then
  tar -C /source/hermes -czf "$destination/hermes.tar.gz" .
fi

printf '{"created_at":"%s","database":"%s","format":1,"includes":["postgres","exports","hermes"]}\n' \
  "$(date -u +%FT%TZ)" "${PGDATABASE:-unknown}" > "$destination/manifest.json"
(
  cd "$destination"
  sha256sum ./* > SHA256SUMS
)

find "$backup_root" -mindepth 1 -maxdepth 1 -type d -mtime "+$retention_days" -exec rm -rf -- {} +
echo "Backup created: $destination"
