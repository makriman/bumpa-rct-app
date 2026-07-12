#!/usr/bin/env sh
set -eu

backup_dir="${BACKUP_DIR:-/backups}"

if [ -L "$backup_dir" ]; then
  echo "Backup directory must not be a symbolic link" >&2
  exit 1
fi

mkdir -p "$backup_dir"

# Backups execute as an explicitly capability-restricted root process so they
# can read private, read-only application volumes with DAC_READ_SEARCH. Keep
# the writable destination root-owned so the backup process does not also need
# DAC_OVERRIDE. The recursive ownership migration handles volumes created by
# older releases, whose image entrypoint assigned them to PostgreSQL uid 70.
chown -R 0:0 -- "$backup_dir"
find "$backup_dir" -type d -exec chmod 0700 {} +
