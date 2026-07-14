#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pin="${TEMPORARY_AUTH_E2E_PIN:-}"
if [[ ! "$pin" =~ ^[0-9]{6}$ ]]; then
  echo "Set TEMPORARY_AUTH_E2E_PIN to the six-digit disposable test PIN" >&2
  exit 2
fi

export COMPOSE_PROJECT_NAME="bumpabestie-temp-auth-${PPID}-$$"
export COMPOSE_ANSI=never
export COMPOSE_PROGRESS=plain
env_file="$(mktemp "${TMPDIR:-/tmp}/bumpabestie-temp-auth.XXXXXX")"
chmod 0600 "$env_file"
output_dir="$(mktemp -d "${TMPDIR:-/tmp}/bumpabestie-temp-auth-results.XXXXXX")"
chmod 0700 "$output_dir"
cleanup() {
  result=$?
  if ((result != 0)); then
    docker compose --env-file "$env_file" ps >&2 || true
    docker compose --env-file "$env_file" logs --no-color --tail=200 >&2 || true
  fi
  docker compose --env-file "$env_file" --profile tools rm --stop --force \
    worker scheduler >/dev/null 2>&1 || true
  docker compose --env-file "$env_file" --profile tools down \
    --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "$env_file"
  rm -rf "$output_dir"
  unset pin verifier otp_secret
  exit "$result"
}
trap cleanup EXIT

otp_secret="$(awk -F= '$1 == "OTP_SECRET" {sub(/^[^=]*=/, ""); print; exit}' .env.example)"
export TEMPORARY_AUTH_E2E_OTP_SECRET="$otp_secret"
export TEMPORARY_AUTH_E2E_PIN="$pin"
verifier="$(python3 - <<'PY'
import hashlib
import hmac
import os

secret = os.environ["TEMPORARY_AUTH_E2E_OTP_SECRET"]
pin = os.environ["TEMPORARY_AUTH_E2E_PIN"]
print(hmac.new(secret.encode(), f"web-login-pin:{pin}".encode(), hashlib.sha256).hexdigest())
PY
)"
unset TEMPORARY_AUTH_E2E_OTP_SECRET
expiry="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone

print((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"))
PY
)"

awk -v verifier="$verifier" -v expiry="$expiry" '
  /^AUTH_LOGIN_MODE=/ { print "AUTH_LOGIN_MODE=temporary_static_pin"; next }
  /^TEMPORARY_WEB_PIN_VERIFIER=/ {
    print "TEMPORARY_WEB_PIN_VERIFIER=" verifier; next
  }
  /^TEMPORARY_WEB_PIN_EXPIRES_AT=/ {
    print "TEMPORARY_WEB_PIN_EXPIRES_AT=" expiry; next
  }
  /^WHATSAPP_BACKEND=/ { print "WHATSAPP_BACKEND=disabled"; next }
  /^EXPOSE_LOCAL_OTP=/ { print "EXPOSE_LOCAL_OTP=false"; next }
  /^NEXT_PUBLIC_DEMO_MODE=/ { print "NEXT_PUBLIC_DEMO_MODE=false"; next }
  { print }
' .env.example > "$env_file"
unset verifier otp_secret

compose=(docker compose --env-file "$env_file")
"${compose[@]}" config --quiet
"${compose[@]}" up -d --build postgres redis
"${compose[@]}" build api
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d --build --wait worker scheduler api web caddy
"${compose[@]}" ps

export TEMPORARY_AUTH_E2E_BASE_URL="http://bumpabestie.localhost:8080"
export TEMPORARY_AUTH_E2E_ENV_FILE="$env_file"
export TEMPORARY_AUTH_E2E_OUTPUT_DIR="$output_dir"
export TEMPORARY_AUTH_E2E_REPOSITORY_ROOT="$ROOT_DIR"
# Playwright's AI error-context snapshot includes password-field values. Keep
# the disposable PIN process-only even when an assertion fails.
export PLAYWRIGHT_NO_COPY_PROMPT=1
(
  cd apps/web
  npm exec -- playwright test --config=playwright.temporary-auth.config.ts
)

echo "PASS real browser/BFF/API/Postgres temporary web-PIN integration"
