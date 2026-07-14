#!/usr/bin/env bash

# Fail-closed containment for a post-boundary application rollback. The caller
# supplies the production Compose command as the global `compose` array. Caddy
# and API containers are removed before any rollback step, and are removed again
# after a failed attempt so neither the target auth mode nor a partial recreate
# can remain reachable.

validated_rollback_container_ids() {
  local output="$1" container_id
  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue
    [[ "$container_id" =~ ^[a-f0-9]{12,64}$ ]] || return 1
    printf '%s\n' "$container_id"
  done <<<"$output"
}

rollback_surface_container_ids() {
  local output
  # The sourcing deployment script owns this deliberately narrow command array.
  # shellcheck disable=SC2154
  output="$("${compose[@]}" ps --all --quiet caddy api)" || return 1
  validated_rollback_container_ids "$output"
}

rollback_labeled_surface_container_ids() {
  local output service
  for service in caddy api; do
    output="$(docker ps --all --quiet \
      --filter label=com.docker.compose.project=bumpabestie \
      --filter "label=com.docker.compose.service=$service")" || return 1
    validated_rollback_container_ids "$output" || return 1
  done
}

remove_rollback_auth_surface() {
  local compose_before="" labeled_before="" compose_after="" labeled_after=""
  local compose_final="" labeled_final="" combined container_id
  local -a container_ids=()

  compose_before="$(rollback_surface_container_ids 2>/dev/null)" || compose_before=""
  labeled_before="$(rollback_labeled_surface_container_ids 2>/dev/null)" || labeled_before=""
  "${compose[@]}" stop --timeout 30 caddy api >/dev/null 2>&1 || true
  "${compose[@]}" rm --force caddy api >/dev/null 2>&1 || true
  compose_after="$(rollback_surface_container_ids 2>/dev/null)" || compose_after=""
  labeled_after="$(rollback_labeled_surface_container_ids 2>/dev/null)" || labeled_after=""
  combined="${compose_before}${compose_before:+$'\n'}${labeled_before}"
  combined="${combined}${combined:+$'\n'}${compose_after}"
  combined="${combined}${combined:+$'\n'}${labeled_after}"
  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue
    container_ids+=("$container_id")
  done <<<"$combined"
  if ((${#container_ids[@]} > 0)); then
    docker rm --force "${container_ids[@]}" >/dev/null 2>&1 || true
  fi

  # Docker's label query is the authoritative proof because it does not depend
  # on Compose metadata lookup. Compose is also checked when available.
  labeled_final="$(rollback_labeled_surface_container_ids)" || return 1
  compose_final="$(rollback_surface_container_ids 2>/dev/null)" || compose_final=""
  [[ -z "$labeled_final" && -z "$compose_final" ]] || return 1
  if ((${#container_ids[@]} > 0)); then
    for container_id in "${container_ids[@]}"; do
      if docker inspect "$container_id" >/dev/null 2>&1; then
        return 1
      fi
    done
  fi
}

run_contained_rollback_attempt() {
  (($# == 2)) || return 2
  local attempt_callback="$1" maintenance_callback="$2" attempt_result

  if ! remove_rollback_auth_surface; then
    "$maintenance_callback" "rollback_auth_surface_containment_failed" || true
    return 125
  fi
  if "$attempt_callback"; then
    return 0
  else
    attempt_result=$?
  fi
  if ! remove_rollback_auth_surface; then
    "$maintenance_callback" "rollback_failure_containment_failed" || true
    return 125
  fi
  "$maintenance_callback" "application_rollback_attempt_failed" || return 125
  return "$attempt_result"
}
