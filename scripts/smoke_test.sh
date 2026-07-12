#!/usr/bin/env bash
set -Eeuo pipefail

scheme="${SMOKE_SCHEME:-http}"
port="${SMOKE_PORT:-8080}"
app_domain="${APP_DOMAIN:-bumpabestie.localhost}"
admin_domain="${ADMIN_DOMAIN:-admin.bumpabestie.localhost}"
research_domain="${RESEARCH_DOMAIN:-research.bumpabestie.localhost}"
api_domain="${API_DOMAIN:-api.bumpabestie.localhost}"
port_suffix=""
if [[ "$scheme" == "http" && "$port" != "80" ]] || [[ "$scheme" == "https" && "$port" != "443" ]]; then
  port_suffix=":$port"
fi

request() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local attempt status
  for ((attempt = 1; attempt <= 30; attempt++)); do
    status="$(curl --silent --show-error --output /tmp/bumpabestie-smoke-body --write-out '%{http_code}' --max-time 10 "$url" || true)"
    if [[ "$status" =~ $expected ]]; then
      echo "PASS $name ($status)"
      return 0
    fi
    sleep 2
  done
  echo "FAIL $name: expected HTTP $expected, received ${status:-none} from $url" >&2
  sed -n '1,20p' /tmp/bumpabestie-smoke-body >&2 || true
  return 1
}

request "API health" "$scheme://$api_domain$port_suffix/health" '^200$'
request "public surface" "$scheme://$app_domain$port_suffix/" '^(200|307|308)$'
request "admin surface" "$scheme://$admin_domain$port_suffix/" '^(200|302|303|307|308|401|403)$'
request "research surface" "$scheme://$research_domain$port_suffix/" '^(200|302|303|307|308|401|403)$'
