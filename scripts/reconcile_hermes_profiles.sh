#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.production}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Hermes reconciliation requires $ENV_FILE" >&2
  exit 2
fi

compose=(docker compose --env-file "$ENV_FILE" -f compose.yaml -f compose.prod.yaml)
if ! "${compose[@]}" ps --status running --quiet hermes | grep -q .; then
  echo "Hermes container is not running" >&2
  exit 1
fi

profile_count="$(
  "${compose[@]}" run --rm --no-deps hermes-import
)"
if [[ ! "$profile_count" =~ ^[0-9]+$ ]]; then
  echo "Hermes profile import did not return a valid count" >&2
  exit 1
fi

runtime_profile_count="$(
  # The single-quoted program executes inside the container; expansion here
  # would be incorrect.
  # shellcheck disable=SC2016
  "${compose[@]}" run --rm --no-deps --entrypoint sh hermes-import -eu -c '
    count=0
    for directory in /opt/data/profiles/tenant_*; do
      [ -d "$directory" ] || continue
      name="${directory##*/}"
      case "$name" in
        *[!a-z0-9_]*) echo "Invalid staged Hermes profile name" >&2; exit 1 ;;
      esac
      for required in .no-skills .env config.yaml SOUL.md; do
        [ -f "$directory/$required" ] || {
          echo "Staged Hermes profile is incomplete" >&2
          exit 1
        }
      done
      count=$((count + 1))
    done
    printf "%s" "$count"
  '
)"
if ((10#$runtime_profile_count < 10#$profile_count)); then
  echo "Hermes runtime contains fewer profiles than the completed import" >&2
  exit 1
fi

# The official boot reconciler creates the dynamic s6 service slots from the
# persistent profile directories. Recreating only this private service avoids
# giving the API a Docker socket or host-control capability.
"${compose[@]}" up -d --no-deps --force-recreate hermes
"${compose[@]}" up -d --wait --wait-timeout 180 hermes

# shellcheck disable=SC2016
"${compose[@]}" exec -T hermes sh -eu -c '
  started=0
  for directory in /opt/data/profiles/tenant_*; do
    [ -d "$directory" ] || continue
    name="${directory##*/}"
    hermes -p "$name" gateway start >/dev/null
    started=$((started + 1))
  done
  printf "%s" "$started"
'

if [[ "$runtime_profile_count" == "0" ]]; then
  echo "Hermes profiles ready: 0"
  exit 0
fi

for _attempt in {1..90}; do
  # shellcheck disable=SC2016
  if "${compose[@]}" exec -T hermes sh -eu -c '
    checked=0
    for directory in /opt/data/profiles/tenant_*; do
      [ -d "$directory" ] || continue
      port="$(sed -n "s/^API_SERVER_PORT=//p" "$directory/.env")"
      key="$(sed -n "s/^API_SERVER_KEY=//p" "$directory/.env")"
      case "$port" in *[!0-9]*|"") exit 1 ;; esac
      [ "${#key}" -ge 8 ] || exit 1
      curl --fail --silent --show-error \
        --header "Authorization: Bearer $key" \
        --max-time 3 \
        "http://127.0.0.1:$port/health/detailed" >/dev/null || exit 1
      checked=$((checked + 1))
    done
    [ "$checked" -eq '"$runtime_profile_count"' ]
  '; then
    echo "Hermes profiles ready: $profile_count"
    exit 0
  fi
  sleep 2
done

echo "Hermes profiles did not become ready" >&2
exit 1
