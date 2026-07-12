#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

source "$ROOT_DIR/scripts/maintenance_lock.sh"
acquire_maintenance_lock

env_file="${ENV_FILE:-.env.production}"
compose=(
  docker compose --env-file "$env_file"
  -f compose.yaml -f compose.prod.yaml
  --profile async --profile tools
)
writer_services=(caddy web api worker scheduler hermes)
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
