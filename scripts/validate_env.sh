#!/usr/bin/env bash
set -Eeuo pipefail

env_file="${1:-.env}"
expected_environment="${2:-local}"

if [[ ! -f "$env_file" ]]; then
  echo "Environment file not found: $env_file" >&2
  exit 2
fi

value_for() {
  local key="$1"
  awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$env_file"
}

required=(APP_ENV APP_DOMAIN ADMIN_DOMAIN RESEARCH_DOMAIN API_DOMAIN JWT_SECRET OTP_SECRET FIELD_ENCRYPTION_KEY INTERNAL_SERVICE_TOKEN COOKIE_SECRET POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB APP_POSTGRES_PASSWORD DATABASE_URL MIGRATION_DATABASE_URL REDIS_URL)
failed=0
for key in "${required[@]}"; do
  value="$(value_for "$key")"
  if [[ -z "$value" ]]; then
    echo "Missing required setting: $key" >&2
    failed=1
  fi
done

app_env="$(value_for APP_ENV)"
if [[ "$app_env" != "$expected_environment" ]]; then
  echo "APP_ENV is '$app_env'; expected '$expected_environment'" >&2
  failed=1
fi

if [[ "$expected_environment" == "production" ]]; then
  secret_keys=(JWT_SECRET OTP_SECRET FIELD_ENCRYPTION_KEY INTERNAL_SERVICE_TOKEN COOKIE_SECRET POSTGRES_PASSWORD APP_POSTGRES_PASSWORD)
  for key in "${secret_keys[@]}"; do
    value="$(value_for "$key")"
    if [[ ${#value} -lt 24 || "$value" == *local-only* || "$value" == *ADD_VALUE* || "$value" == *change-me* ]]; then
      echo "$key is missing, too short, or still uses a local placeholder" >&2
      failed=1
    fi
  done

  if [[ "$(value_for SESSION_COOKIE_SECURE)" != "true" ]]; then
    echo "SESSION_COOKIE_SECURE must be true in production" >&2
    failed=1
  fi
  if [[ "$(value_for CADDY_SITE_SCHEME)" != "https" ]]; then
    echo "CADDY_SITE_SCHEME must be https in production" >&2
    failed=1
  fi
  if grep -Eq '^(DEV_FIXED_OTP|DEV_OTP_SINK)=' "$env_file"; then
    echo "Development OTP controls must not be present in production" >&2
    failed=1
  fi
  for key in WHATSAPP_BACKEND AGENT_BACKEND BUMPA_BACKEND; do
    if [[ "$(value_for "$key")" == "mock" || -z "$(value_for "$key")" ]]; then
      echo "$key must select an explicitly configured live adapter in production" >&2
      failed=1
    fi
  done
  if [[ "$(value_for CADDY_BIND_ADDRESS)" != "0.0.0.0" ]]; then
    echo "CADDY_BIND_ADDRESS must be 0.0.0.0 in production" >&2
    failed=1
  fi
fi

if ((failed)); then
  exit 2
fi

echo "Environment contract valid for $expected_environment: $env_file"
