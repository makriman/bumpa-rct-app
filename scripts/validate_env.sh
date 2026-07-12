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

required=(
  APP_ENV APP_DOMAIN WWW_DOMAIN ADMIN_DOMAIN RESEARCH_DOMAIN API_DOMAIN
  PUBLIC_ORIGIN ADMIN_ORIGIN RESEARCH_ORIGIN API_ORIGIN API_BASE_URL
  TRUSTED_HOSTS CORS_ALLOWED_ORIGINS
  JWT_SECRET OTP_SECRET FIELD_ENCRYPTION_KEY INTERNAL_SERVICE_TOKEN COOKIE_SECRET
  POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB APP_POSTGRES_PASSWORD
  DATABASE_URL MIGRATION_DATABASE_URL SYNC_DATABASE_URL REDIS_URL
  WHATSAPP_BACKEND AGENT_BACKEND BUMPA_BACKEND
)
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
  if [[ "$(value_for EXPOSE_LOCAL_OTP)" != "false" || "$(value_for SEED_DEMO_DATA)" != "false" ]]; then
    echo "EXPOSE_LOCAL_OTP and SEED_DEMO_DATA must be false in production" >&2
    failed=1
  fi
  if [[ "$(value_for NEXT_PUBLIC_DEMO_MODE)" != "false" ]]; then
    echo "NEXT_PUBLIC_DEMO_MODE must be false in production" >&2
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
  whatsapp_backend="$(value_for WHATSAPP_BACKEND)"
  agent_backend="$(value_for AGENT_BACKEND)"
  bumpa_backend="$(value_for BUMPA_BACKEND)"
  [[ "$whatsapp_backend" =~ ^(disabled|meta)$ ]] || {
    echo "WHATSAPP_BACKEND must be disabled or meta in production" >&2
    failed=1
  }
  [[ "$agent_backend" =~ ^(disabled|hermes)$ ]] || {
    echo "AGENT_BACKEND must be disabled or hermes in production" >&2
    failed=1
  }
  [[ "$bumpa_backend" =~ ^(disabled|bumpa)$ ]] || {
    echo "BUMPA_BACKEND must be disabled or bumpa in production" >&2
    failed=1
  }
  if [[ "$(value_for ASYNC_RUNTIME_ENABLED)" != "false" ]]; then
    echo "ASYNC_RUNTIME_ENABLED must remain false until the production queue is installed" >&2
    failed=1
  fi
  if [[ "$(value_for CADDY_BIND_ADDRESS)" != "0.0.0.0" ]]; then
    echo "CADDY_BIND_ADDRESS must be 0.0.0.0 in production" >&2
    failed=1
  fi
  if [[ "$(value_for CADDY_HTTP_PORT)" != "80" || "$(value_for CADDY_HTTPS_PORT)" != "443" ]]; then
    echo "CADDY_HTTP_PORT and CADDY_HTTPS_PORT must be 80 and 443 in production" >&2
    failed=1
  fi
fi

if ((failed)); then
  exit 2
fi

echo "Environment contract valid for $expected_environment: $env_file"
