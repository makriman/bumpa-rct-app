#!/usr/bin/env bash

promotion_state_path_is_safe() {
  local state_file="$1"
  local lock_path="${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}"
  local state_name suffix
  [[ "${state_file%/*}" == "${lock_path%/*}" ]] || return 1
  state_name="${state_file##*/}"
  suffix="${state_name#"${lock_path##*/}.promotion-state."}"
  [[ "$state_name" == "${lock_path##*/}.promotion-state.$suffix" \
    && "$suffix" =~ ^[0-9]+$ ]]
}

promotion_file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

write_promotion_state() (
  local state_file="$1"
  local state="$2"
  local state_tmp=""
  trap 'if [[ -n "$state_tmp" ]]; then rm -f -- "$state_tmp"; fi' EXIT
  promotion_state_path_is_safe "$state_file" || return 1
  case "$state" in
    PRE_BOUNDARY | FORWARD_BOUNDARY | PREVIOUS_RESTORED | HYBRID_PERSISTED | COMMITTED) ;;
    *) return 1 ;;
  esac
  if [[ -L "$state_file" ]]; then
    return 1
  fi
  state_tmp="$(mktemp "${state_file}.tmp.XXXXXX")" || return 1
  chmod 0600 "$state_tmp" || return 1
  printf '%s\n' "$state" > "$state_tmp" || return 1
  sync -f "$state_tmp" 2>/dev/null || sync
  mv -f "$state_tmp" "$state_file" || return 1
  sync -f "${state_file%/*}" 2>/dev/null || sync
  state_tmp=""
)

read_promotion_state() {
  local state_file="$1"
  local state
  promotion_state_path_is_safe "$state_file" || return 1
  [[ -f "$state_file" && ! -L "$state_file" \
    && "$(promotion_file_mode "$state_file")" == "600" ]] || return 1
  state="$(sed -n '1p' "$state_file")"
  [[ "$(wc -l < "$state_file" | tr -d ' ')" == "1" ]] || return 1
  case "$state" in
    PRE_BOUNDARY | FORWARD_BOUNDARY | PREVIOUS_RESTORED | HYBRID_PERSISTED | COMMITTED)
      printf '%s\n' "$state"
      ;;
    *) return 1 ;;
  esac
}

maintenance_required_path() {
  printf '%s.maintenance-required\n' \
    "${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}"
}

mark_maintenance_required() {
  local reason="$1"
  local required_file required_tmp=""
  required_file="$(maintenance_required_path)"
  if [[ -L "$required_file" ]]; then
    return 1
  fi
  required_tmp="$(mktemp "${required_file}.tmp.XXXXXX")" || return 1
  if ! chmod 0600 "$required_tmp" \
    || ! printf '%s\n' "$reason" > "$required_tmp" \
    || ! mv -f "$required_tmp" "$required_file"; then
    rm -f -- "$required_tmp"
    return 1
  fi
  sync -f "${required_file%/*}" 2>/dev/null || sync
}

assert_maintenance_clear() {
  local required_file state_file
  required_file="$(maintenance_required_path)"
  if [[ -e "$required_file" ]]; then
    echo "Maintenance-required interlock is active; reconcile the recorded promotion before continuing" >&2
    return 78
  fi
  for state_file in \
    "${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}".promotion-state.*; do
    if [[ -e "$state_file" || -L "$state_file" ]]; then
      echo "An incomplete promotion journal is present; reconcile it before continuing" >&2
      return 78
    fi
  done
}
