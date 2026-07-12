#!/usr/bin/env bash
set -Eeuo pipefail

env_file="${1:-.env}"
expected_environment="${2:-local}"

if [[ ! -f "$env_file" ]]; then
  echo "Environment file not found: $env_file" >&2
  exit 2
fi

failed=0
duplicate_keys="$(
  awk -F= '
    /^[A-Za-z_][A-Za-z0-9_]*=/ { count[$1]++ }
    END { for (key in count) if (count[key] > 1) print key }
  ' "$env_file" | sort
)"
if [[ -n "$duplicate_keys" ]]; then
  while IFS= read -r key; do
    echo "Duplicate environment setting: $key" >&2
  done <<< "$duplicate_keys"
  failed=1
fi

malformed_lines="$(
  awk '
    /^[[:space:]]*($|#)/ { next }
    !/^[A-Za-z_][A-Za-z0-9_]*=/ { print NR }
  ' "$env_file"
)"
if [[ -n "$malformed_lines" ]]; then
  while IFS= read -r line_number; do
    echo "Malformed environment assignment at line $line_number" >&2
  done <<< "$malformed_lines"
  failed=1
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
  release_keys=(
    GHCR_OWNER DEPLOY_REF IMAGE_TAG INFRA_IMAGE_TAG
    API_IMAGE WEB_IMAGE CADDY_IMAGE POSTGRES_IMAGE BACKUP_IMAGE
  )
  for key in "${release_keys[@]}"; do
    if [[ -z "$(value_for "$key")" ]]; then
      echo "Missing production release setting: $key" >&2
      failed=1
    fi
  done

  deploy_ref="$(value_for DEPLOY_REF)"
  image_tag="$(value_for IMAGE_TAG)"
  infra_image_tag="$(value_for INFRA_IMAGE_TAG)"
  if [[ ! "$deploy_ref" =~ ^[0-9a-f]{40}$ ]]; then
    echo "DEPLOY_REF must be a full lowercase 40-character commit SHA" >&2
    failed=1
  fi
  if [[ "$image_tag" != "sha-$deploy_ref" ]]; then
    echo "IMAGE_TAG must equal sha-DEPLOY_REF" >&2
    failed=1
  fi
  if [[ ! "$infra_image_tag" =~ ^sha-[0-9a-f]{40}$ ]]; then
    echo "INFRA_IMAGE_TAG must be an immutable sha-<full-commit-sha> tag" >&2
    failed=1
  fi
  ghcr_owner="$(value_for GHCR_OWNER)"
  if [[ ! "$ghcr_owner" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]]; then
    echo "GHCR_OWNER must be a lowercase registry owner" >&2
    failed=1
  fi

  validate_image_ref() {
    local key="$1"
    local repository="$2"
    local value prefix digest
    value="$(value_for "$key")"
    prefix="ghcr.io/$ghcr_owner/$repository@sha256:"
    if [[ "$value" != "$prefix"* ]]; then
      echo "$key must use the expected GHCR repository and an immutable digest" >&2
      failed=1
      return
    fi
    digest="${value#"$prefix"}"
    if [[ ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
      echo "$key must end with a lowercase sha256 digest" >&2
      failed=1
    fi
  }
  validate_image_ref API_IMAGE bumpabestie-api
  validate_image_ref WEB_IMAGE bumpabestie-web
  validate_image_ref CADDY_IMAGE bumpabestie-caddy
  validate_image_ref POSTGRES_IMAGE bumpabestie-postgres
  validate_image_ref BACKUP_IMAGE bumpabestie-backup

  origin_keys=(
    PUBLIC_ORIGIN ADMIN_ORIGIN RESEARCH_ORIGIN API_ORIGIN
    NEXT_PUBLIC_APP_URL NEXT_PUBLIC_API_BASE_URL
  )
  for key in "${origin_keys[@]}"; do
    if [[ "$(value_for "$key")" != https://* ]]; then
      echo "$key must use HTTPS in production" >&2
      failed=1
    fi
  done
  if [[ "$(value_for CORS_ALLOWED_ORIGINS)" == *http://* ]]; then
    echo "CORS_ALLOWED_ORIGINS must contain only HTTPS origins in production" >&2
    failed=1
  fi

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
