#!/usr/bin/env bash
set -Eeuo pipefail

emit_backup_alert() {
  local status="$1"
  local hook="${BUMPABESTIE_ALERT_HOOK:-}"
  [[ -n "$hook" && "$hook" == /* && -f "$hook" && ! -L "$hook" && -x "$hook" ]] || return 0
  local event_json
  event_json="$(printf '{"event":"backup","occurred_at":"%s","status":"%s"}' \
    "$(date -u +%FT%TZ)" "$status")"
  if ! BUMPABESTIE_ALERT_EVENT=backup "$hook" <<<"$event_json"; then
    echo "Backup outcome alert could not be delivered" >&2
  fi
}

alert_early_failure() {
  local result=$?
  trap - EXIT
  if ((result != 0)); then
    emit_backup_alert failure
  fi
  exit "$result"
}
trap alert_early_failure EXIT

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

source "$ROOT_DIR/scripts/maintenance_lock.sh"
acquire_maintenance_lock
coordinator_state="${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}.coordinator-state.json"
if [[ -e "$coordinator_state" || -L "$coordinator_state" ]]; then
  echo "A stable promotion is incomplete; scheduled backup is blocked" >&2
  exit 78
fi
source "$ROOT_DIR/scripts/promotion_state.sh"
assert_maintenance_clear

env_file="${ENV_FILE:-.env.production}"
compose=(
  docker compose --env-file "$env_file"
  -f compose.yaml -f compose.prod.yaml
  --profile async --profile tools
)
# Caddy and the web frontend do not write the database-linked export or Hermes
# volumes. Keep them serving the application shell while every process that can
# mutate backup state is quiesced.
writer_services=(api worker scheduler hermes)
running_services=()
writers_quiesced=0

resume_writers() {
  local result=$?
  trap - EXIT
  if ((writers_quiesced)) && ((${#running_services[@]} > 0)); then
    if ! "${compose[@]}" up -d --wait "${running_services[@]}"; then
      echo "Backup completed or failed, but one or more application services did not resume" >&2
      result=1
    fi
  fi
  if ((result == 0)); then
    emit_backup_alert success
  else
    emit_backup_alert failure
  fi
  exit "$result"
}
trap resume_writers EXIT

for service in "${writer_services[@]}"; do
  if [[ -n "$("${compose[@]}" ps --status running -q "$service")" ]]; then
    running_services+=("$service")
  fi
done

if ((${#running_services[@]} > 0)); then
  writers_quiesced=1
  "${compose[@]}" stop --timeout 60 "${running_services[@]}"
fi

"${compose[@]}" run --rm --no-deps backup-data-init
"${compose[@]}" run --rm --no-deps backup
