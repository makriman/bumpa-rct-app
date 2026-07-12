#!/usr/bin/env bash
set -Eeuo pipefail

env_file=""
if (($# > 0)); then
  if [[ $# -ne 2 || "$1" != "--env-file" ]]; then
    echo "Usage: offsite_backup.sh [--env-file PATH]" >&2
    exit 2
  fi
  env_file="$2"
fi

if [[ -z "${OFFSITE_BACKUP_SCRIPT:-}" && -n "$env_file" ]]; then
  if [[ ! -f "$env_file" || -L "$env_file" ]]; then
    echo "Off-host configuration file is missing or invalid" >&2
    exit 2
  fi

  configured_value=""
  configured_count=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    case "$line" in
      OFFSITE_BACKUP_SCRIPT=*)
        configured_value="${line#OFFSITE_BACKUP_SCRIPT=}"
        configured_count=$((configured_count + 1))
        ;;
    esac
  done < "$env_file"

  if ((configured_count > 1)); then
    echo "OFFSITE_BACKUP_SCRIPT is duplicated" >&2
    exit 2
  fi
  OFFSITE_BACKUP_SCRIPT="$configured_value"
fi

if [[ -z "${OFFSITE_BACKUP_SCRIPT:-}" ]]; then
  echo "OFFSITE_BACKUP_SCRIPT is not configured; local backup completed, off-host durability remains pending" >&2
  exit 0
fi
if [[ "$OFFSITE_BACKUP_SCRIPT" != /* || ! -x "$OFFSITE_BACKUP_SCRIPT" ]]; then
  echo "OFFSITE_BACKUP_SCRIPT must be an absolute executable path" >&2
  exit 2
fi

# The operator-owned script normally invokes restic with credentials held outside
# the repository. Only the literal executable path is parsed; the env file is never
# sourced or exported, so application secrets cannot enter the hook environment.
exec "$OFFSITE_BACKUP_SCRIPT"
