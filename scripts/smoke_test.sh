#!/usr/bin/env bash
set -Eeuo pipefail

scheme="${SMOKE_SCHEME:-http}"
port="${SMOKE_PORT:-8080}"
overall_timeout_seconds="${SMOKE_OVERALL_TIMEOUT_SECONDS:-60}"
origin_address="${SMOKE_ORIGIN_ADDRESS:-}"
app_domain="${APP_DOMAIN:-bumpabestie.localhost}"
www_domain="${WWW_DOMAIN:-www.bumpabestie.localhost}"
admin_domain="${ADMIN_DOMAIN:-admin.bumpabestie.localhost}"
research_domain="${RESEARCH_DOMAIN:-research.bumpabestie.localhost}"
api_domain="${API_DOMAIN:-api.bumpabestie.localhost}"

if [[ ! "$overall_timeout_seconds" =~ ^[0-9]+$ ]] \
  || ((10#$overall_timeout_seconds <= 0)); then
  echo "SMOKE_OVERALL_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi

umask 077
body_file="$(mktemp "${TMPDIR:-/tmp}/bumpabestie-smoke-body.XXXXXX")"
cleanup() {
  rm -f "$body_file"
}
trap cleanup EXIT

port_suffix=""
if [[ "$scheme" == "http" && "$port" != "80" ]] || [[ "$scheme" == "https" && "$port" != "443" ]]; then
  port_suffix=":$port"
fi
deadline=$((SECONDS + 10#$overall_timeout_seconds))

request() {
  local name="$1"
  local host="$2"
  local path="$3"
  local expected="$4"
  local url="$scheme://$host$port_suffix$path"
  local status=""
  local remaining request_timeout sleep_seconds
  local -a curl_args=(
    --silent
    --show-error
    --output "$body_file"
    --write-out '%{http_code}'
  )
  if [[ -n "$origin_address" ]]; then
    curl_args+=(--noproxy '*' --resolve "$host:$port:$origin_address")
  fi

  while ((SECONDS < deadline)); do
    remaining=$((deadline - SECONDS))
    request_timeout=10
    if ((remaining < request_timeout)); then
      request_timeout=$remaining
    fi
    : > "$body_file"
    status="$(curl "${curl_args[@]}" --max-time "$request_timeout" "$url" || true)"
    if [[ "$status" =~ $expected ]]; then
      echo "PASS $name ($status)"
      return 0
    fi

    remaining=$((deadline - SECONDS))
    if ((remaining <= 0)); then
      break
    fi
    sleep_seconds=2
    if ((remaining < sleep_seconds)); then
      sleep_seconds=$remaining
    fi
    sleep "$sleep_seconds"
  done
  echo "FAIL $name: expected HTTP $expected, received ${status:-none} from $url" >&2
  sed -n '1,20p' "$body_file" >&2 || true
  return 1
}

request "API health" "$api_domain" "/health" '^200$'
request "API readiness" "$api_domain" "/health/ready" '^200$'
request "public surface" "$app_domain" "/" '^(200|307|308)$'
request "www surface" "$www_domain" "/" '^(200|307|308)$'
request "admin authentication boundary" "$admin_domain" "/" '^(302|303|307|308)$'
request "research authentication boundary" "$research_domain" "/" '^(302|303|307|308)$'
