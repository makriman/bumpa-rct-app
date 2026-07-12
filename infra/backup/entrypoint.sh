#!/usr/bin/env sh
set -eu

install -d -m 0700 -o 70 -g 70 "${BACKUP_DIR:-/backups}"

if [ "${1:-}" = "/usr/local/bin/restore.sh" ]; then
  exec "$@"
fi

exec gosu 70:70 "$@"
