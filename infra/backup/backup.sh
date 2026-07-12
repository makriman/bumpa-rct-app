#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

backup_root="${BACKUP_DIR:-/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$backup_root/$timestamp"
working="$backup_root/.${timestamp}.$$.partial"

cleanup_partial() {
  rm -rf -- "$working"
}
trap cleanup_partial EXIT

if ! [[ "$retention_days" =~ ^[0-9]+$ ]] || ((retention_days < 1)); then
  echo "BACKUP_RETENTION_DAYS must be a positive integer" >&2
  exit 2
fi

if [[ -e "$destination" ]]; then
  echo "A completed backup already exists for timestamp $timestamp" >&2
  exit 1
fi
mkdir "$working"
pg_dump --format=custom --compress=9 --no-owner --no-privileges --file "$working/postgres.dump"

schema_revision="$(
  psql -X --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command 'SELECT version_num FROM alembic_version LIMIT 1' 2>/dev/null || true
)"
dump_version="$(pg_dump --version)"

if [[ -d /source/exports ]]; then
  tar -C /source/exports -czf "$working/exports.tar.gz" .
fi

if [[ -d /source/hermes ]]; then
  tar -C /source/hermes -czf "$working/hermes.tar.gz" .
fi

psql -X --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --set="created_at=$(date -u +%FT%TZ)" \
  --set="database=${PGDATABASE:-unknown}" \
  --set="dump_version=$dump_version" \
  --set="schema_revision=${schema_revision:-unknown}" \
  --set="application_revision=${APPLICATION_REVISION:-unknown}" \
  --set="backup_image_tag=${BACKUP_IMAGE_TAG:-unknown}" \
  --set="backup_image_ref=${BACKUP_IMAGE_REF:-unknown}" \
  <<'SQL' > "$working/manifest.json"
SELECT jsonb_pretty(
  jsonb_build_object(
    $$format$$, 2,
    $$created_at$$, :'created_at',
    $$database$$, :'database',
    $$postgres$$, jsonb_build_object(
      $$server_version$$, current_setting($$server_version$$),
      $$server_version_num$$, current_setting($$server_version_num$$),
      $$dump_version$$, :'dump_version'
    ),
    $$schema_revision$$, :'schema_revision',
    $$application_revision$$, :'application_revision',
    $$backup_image_tag$$, :'backup_image_tag',
    $$backup_image_ref$$, :'backup_image_ref',
    $$includes$$, jsonb_build_array($$postgres$$, $$exports$$, $$hermes$$)
  )
);
SQL
(
  cd "$working"
  sha256sum ./* > SHA256SUMS
  sha256sum --check SHA256SUMS
)

mv --no-target-directory "$working" "$destination"
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '.*.partial' -mtime +1 -exec rm -rf -- {} +
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -mtime "+$retention_days" -exec rm -rf -- {} +
echo "Backup created: $destination"
