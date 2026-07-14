#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/rollback_containment.sh
source "$ROOT_DIR/scripts/rollback_containment.sh"

target_caddy_id="$(printf 'a%.0s' {1..64})"
target_api_id="$(printf 'b%.0s' {1..64})"
partial_caddy_id="$(printf 'c%.0s' {1..64})"
partial_api_id="$(printf 'd%.0s' {1..64})"
surface_ids=()
compose_removal_failure=0
compose_lookup_failure=0
docker_ps_fail_on_call=0
docker_ps_counter_file=""
failure_stage=""
marker_file=""
attempt_log=""
compose=(fake_compose)

fake_compose() {
  local command="${1:-}"
  shift || true
  case "$command" in
    ps)
      if ((compose_lookup_failure)); then
        return 1
      fi
      if ((${#surface_ids[@]} > 0)); then
        printf '%s\n' "${surface_ids[@]}"
      fi
      ;;
    stop | rm)
      if ((compose_removal_failure)); then
        return 1
      fi
      if [[ "$command" == "rm" ]]; then
        surface_ids=()
      fi
      ;;
    *)
      return 2
      ;;
  esac
}

docker() {
  local command="${1:-}" candidate existing docker_ps_call
  shift || true
  case "$command" in
    ps)
      docker_ps_call="$(cat "$docker_ps_counter_file")"
      docker_ps_call=$((docker_ps_call + 1))
      printf '%s\n' "$docker_ps_call" >"$docker_ps_counter_file"
      if ((docker_ps_fail_on_call == docker_ps_call)); then
        return 1
      fi
      if ((${#surface_ids[@]} > 0)); then
        printf '%s\n' "${surface_ids[@]}"
      fi
      ;;
    rm)
      [[ "${1:-}" == "--force" ]] && shift
      for candidate in "$@"; do
        local -a retained=()
        if ((${#surface_ids[@]} > 0)); then
          for existing in "${surface_ids[@]}"; do
            [[ "$existing" == "$candidate" ]] || retained+=("$existing")
          done
        fi
        if ((${#retained[@]} > 0)); then
          surface_ids=("${retained[@]}")
        else
          surface_ids=()
        fi
      done
      ;;
    inspect)
      candidate="${1:-}"
      if ((${#surface_ids[@]} > 0)); then
        for existing in "${surface_ids[@]}"; do
          [[ "$existing" == "$candidate" ]] && return 0
        done
      fi
      return 1
      ;;
    *)
      return 2
      ;;
  esac
}

mark_test_maintenance() {
  printf '%s\n' "$1" >"$marker_file"
}

injected_rollback_attempt() {
  ((${#surface_ids[@]} == 0)) || return 90
  case "$failure_stage" in
    pull)
      attempt_log="pull"
      return 71
      ;;
    auth-secret-init)
      attempt_log="pull auth-secret-init"
      return 72
      ;;
    recreation)
      attempt_log="pull auth-secret-init recreation"
      surface_ids=("$partial_caddy_id" "$partial_api_id")
      return 73
      ;;
    success)
      attempt_log="pull auth-secret-init recreation smoke"
      surface_ids=("$partial_caddy_id" "$partial_api_id")
      return 0
      ;;
    *)
      return 91
      ;;
  esac
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
docker_ps_counter_file="$tmp/docker-ps-calls"
for failure_stage in pull auth-secret-init recreation; do
  marker_file="$tmp/maintenance-$failure_stage"
  surface_ids=("$target_caddy_id" "$target_api_id")
  compose_removal_failure=0
  compose_lookup_failure=0
  printf '0\n' >"$docker_ps_counter_file"
  docker_ps_fail_on_call=0
  attempt_log=""
  set +e
  run_contained_rollback_attempt \
    injected_rollback_attempt mark_test_maintenance
  result=$?
  set -e
  [[ "$result" =~ ^7[1-3]$ ]]
  ((${#surface_ids[@]} == 0))
  test -f "$marker_file"
  test "$(cat "$marker_file")" = application_rollback_attempt_failed
  case "$failure_stage" in
    pull) test "$attempt_log" = pull ;;
    auth-secret-init) test "$attempt_log" = "pull auth-secret-init" ;;
    recreation) test "$attempt_log" = "pull auth-secret-init recreation" ;;
  esac
done

# Compose failures cannot defeat containment while Docker can still address the
# recorded container IDs directly.
failure_stage=pull
marker_file="$tmp/maintenance-compose-fallback"
surface_ids=("$target_caddy_id" "$target_api_id")
compose_removal_failure=1
compose_lookup_failure=1
printf '0\n' >"$docker_ps_counter_file"
docker_ps_fail_on_call=1
set +e
run_contained_rollback_attempt injected_rollback_attempt mark_test_maintenance
result=$?
set -e
test "$result" = 71
((${#surface_ids[@]} == 0))
test -f "$marker_file"

# If the final independent Docker lookup fails, containment cannot be proven and
# the operation must remain interlocked even if best-effort removals succeeded.
failure_stage=pull
marker_file="$tmp/maintenance-final-proof"
surface_ids=("$target_caddy_id" "$target_api_id")
compose_removal_failure=1
compose_lookup_failure=1
printf '0\n' >"$docker_ps_counter_file"
docker_ps_fail_on_call=5
set +e
run_contained_rollback_attempt injected_rollback_attempt mark_test_maintenance
result=$?
set -e
test "$result" = 125
((${#surface_ids[@]} == 0))
test "$(cat "$marker_file")" = rollback_auth_surface_containment_failed

# A successful prior-boundary recreate remains available and does not set the
# maintenance interlock.
failure_stage=success
marker_file="$tmp/maintenance-success"
surface_ids=("$target_caddy_id" "$target_api_id")
compose_removal_failure=0
compose_lookup_failure=0
printf '0\n' >"$docker_ps_counter_file"
docker_ps_fail_on_call=0
run_contained_rollback_attempt injected_rollback_attempt mark_test_maintenance
test "${surface_ids[*]}" = "$partial_caddy_id $partial_api_id"
test ! -e "$marker_file"

echo 'Rollback auth-surface containment contract passed.'
