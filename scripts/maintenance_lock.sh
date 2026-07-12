#!/usr/bin/env bash

# Source this file, then call acquire_maintenance_lock before reading mutable
# deployment state. The open descriptor remains owned by the calling shell, so
# the exclusive lock is held until that workflow exits.
acquire_maintenance_lock() {
  local lock_path="${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}"
  local wait_seconds="${BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS:-900}"
  local lock_dir

  if [[ "$lock_path" != /* ]]; then
    echo "BUMPABESTIE_MAINTENANCE_LOCK must be an absolute path" >&2
    return 2
  fi
  if ! [[ "$wait_seconds" =~ ^[0-9]+$ ]]; then
    echo "BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS must be a non-negative integer" >&2
    return 2
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo "flock is required for serialized maintenance workflows" >&2
    return 2
  fi

  lock_dir="${lock_path%/*}"
  if [[ ! -d "$lock_dir" || -L "$lock_dir" ]]; then
    echo "Maintenance lock directory is missing or unsafe: $lock_dir" >&2
    return 2
  fi
  if [[ -L "$lock_path" ]]; then
    echo "Maintenance lock file must not be a symbolic link" >&2
    return 2
  fi

  umask 077
  if ! exec 9>>"$lock_path"; then
    echo "Unable to open maintenance lock: $lock_path" >&2
    return 2
  fi
  if ! flock -w "$wait_seconds" 9; then
    exec 9>&-
    echo "Another maintenance workflow still holds $lock_path after ${wait_seconds}s" >&2
    return 75
  fi
}
