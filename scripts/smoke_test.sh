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
header_file="$(mktemp "${TMPDIR:-/tmp}/bumpabestie-smoke-headers.XXXXXX")"
cleanup() {
  rm -f "$body_file" "$header_file"
}

verify_document_csp() {
  local host="$1"
  local path="$2"
  local url="$scheme://$host$port_suffix$path"
  local previous_nonce=""
  local status remaining request_timeout csp_count csp script_source nonce
  local cache_control script_tags
  local -a curl_args=(
    --silent
    --show-error
    --dump-header "$header_file"
    --output "$body_file"
    --write-out '%{http_code}'
  )
  if [[ -n "$origin_address" ]]; then
    curl_args+=(--noproxy '*' --resolve "$host:$port:$origin_address")
  fi

  for _ in 1 2; do
    remaining=$((deadline - SECONDS))
    if ((remaining <= 0)); then
      echo "FAIL document CSP: shared smoke deadline elapsed" >&2
      return 1
    fi
    request_timeout=10
    if ((remaining < request_timeout)); then
      request_timeout=$remaining
    fi
    : > "$body_file"
    : > "$header_file"
    status="$(curl "${curl_args[@]}" --max-time "$request_timeout" "$url" || true)"
    if [[ "$status" != 200 ]]; then
      echo "FAIL document CSP: expected HTTP 200, received ${status:-none} from $url" >&2
      return 1
    fi

    csp_count="$(grep -Eic '^content-security-policy:' "$header_file" || true)"
    if [[ "$csp_count" != 1 ]]; then
      echo "FAIL document CSP: expected exactly one policy header, received $csp_count" >&2
      return 1
    fi
    csp="$(
      awk '
        tolower($1) == "content-security-policy:" {
          sub(/^[^:]*:[[:space:]]*/, "")
          sub(/\r$/, "")
          print
        }
      ' "$header_file"
    )"
    script_source="$(
      tr ';' '\n' <<<"$csp" \
        | sed -nE 's/^[[:space:]]*(script-src[[:space:]].*)$/\1/p'
    )"
    if [[ "$script_source" != *"'strict-dynamic'"* \
      || "$script_source" == *"'unsafe-inline'"* ]]; then
      echo "FAIL document CSP: script-src is not strict and nonce-gated" >&2
      return 1
    fi
    if [[ "$csp" != *"style-src-attr 'unsafe-inline'"* ]]; then
      echo "FAIL document CSP: scoped style attribute compatibility is missing" >&2
      return 1
    fi
    nonce="$(sed -nE "s/.*'nonce-([^']+)'.*/\1/p" <<<"$script_source")"
    if [[ ! "$nonce" =~ ^[A-Za-z0-9+/_-]{20,}={0,2}$ ]]; then
      echo "FAIL document CSP: nonce is missing or malformed" >&2
      return 1
    fi
    if [[ -n "$previous_nonce" && "$nonce" == "$previous_nonce" ]]; then
      echo "FAIL document CSP: nonce was reused across document requests" >&2
      return 1
    fi
    if grep -Eiq '^x-nonce:' "$header_file"; then
      echo "FAIL document CSP: internal nonce header leaked to the client" >&2
      return 1
    fi
    cache_control="$(
      awk '
        tolower($1) == "cache-control:" {
          sub(/^[^:]*:[[:space:]]*/, "")
          sub(/\r$/, "")
          print
        }
      ' "$header_file"
    )"
    if [[ "$cache_control" != *"no-store"* ]]; then
      echo "FAIL document CSP: nonce-bearing response is cacheable" >&2
      return 1
    fi
    script_tags="$(grep -Eo '<script[^>]*>' "$body_file" || true)"
    if [[ -z "$script_tags" ]]; then
      echo "FAIL document CSP: response did not contain framework scripts" >&2
      return 1
    fi
    while IFS= read -r script_tag; do
      if [[ "$script_tag" != *"nonce=\"$nonce\""* ]]; then
        echo "FAIL document CSP: rendered script is missing the response nonce" >&2
        return 1
      fi
    done <<<"$script_tags"
    previous_nonce="$nonce"
  done
  echo "PASS document nonce CSP (2 unique nonces)"
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
request "www canonical redirect" "$www_domain" "/" '^308$'
request "admin authentication boundary" "$admin_domain" "/" '^(302|303|307|308)$'
request "research authentication boundary" "$research_domain" "/" '^(302|303|307|308)$'
verify_document_csp "$app_domain" "/"
