#!/usr/bin/env bash

# Source this file, then call acquire_maintenance_lock before reading mutable
# deployment state. The open descriptor remains owned by the calling shell, so
# the exclusive lock is held until that workflow exits.
acquire_maintenance_lock() {
  local lock_path="${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}"
  local wait_seconds="${BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS:-900}"
  local inherited_fd="${BUMPABESTIE_MAINTENANCE_LOCK_FD:-}"
  local lock_dir inherited_target lock_target inherited_identity lock_identity

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

  # A release promotion must keep this lock while it checks out the target,
  # atomically selects its pointers, and execs the target deploy script. Bash
  # descriptors survive exec; validate the inherited descriptor points to the
  # configured lock and still owns it before treating acquisition as re-entrant.
  if [[ -n "$inherited_fd" ]]; then
    if [[ ! "$inherited_fd" =~ ^[0-9]+$ ]] \
      || [[ ! -f "/proc/$BASHPID/fd/$inherited_fd" ]] \
      || [[ ! -f "$lock_path" ]]; then
      echo "Inherited maintenance lock descriptor is invalid" >&2
      return 2
    fi
    inherited_target="$(readlink -f "/proc/$BASHPID/fd/$inherited_fd")"
    lock_target="$(readlink -f "$lock_path")"
    inherited_identity="$(stat -Lc '%d:%i' "/proc/$BASHPID/fd/$inherited_fd")"
    lock_identity="$(stat -Lc '%d:%i' "$lock_path")"
    if [[ "$inherited_target" != "$lock_target" \
      || "$inherited_identity" != "$lock_identity" ]]; then
      echo "Inherited maintenance lock descriptor is not the active configured lock" >&2
      return 2
    fi
    # Re-flocking the same open-file description cannot prove it was locked.
    # A separately opened descriptor must be excluded by the inherited lock.
    if ! exec 8>>"$lock_path"; then
      return 2
    fi
    if flock -n 8; then
      flock -u 8 || true
      exec 8>&-
      echo "Inherited maintenance lock descriptor was not already locked" >&2
      return 2
    fi
    exec 8>&-
    return 0
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
  export BUMPABESTIE_MAINTENANCE_LOCK_FD=9
}
