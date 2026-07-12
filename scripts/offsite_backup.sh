#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${OFFSITE_BACKUP_SCRIPT:-}" ]]; then
  echo "OFFSITE_BACKUP_SCRIPT is not configured; local backup completed, off-host durability remains pending" >&2
  exit 0
fi
if [[ "$OFFSITE_BACKUP_SCRIPT" != /* || ! -x "$OFFSITE_BACKUP_SCRIPT" ]]; then
  echo "OFFSITE_BACKUP_SCRIPT must be an absolute executable path" >&2
  exit 2
fi

# The operator-owned script normally invokes restic with credentials held outside
# the repository. Passing an executable path avoids evaluating shell from .env.
exec "$OFFSITE_BACKUP_SCRIPT"
