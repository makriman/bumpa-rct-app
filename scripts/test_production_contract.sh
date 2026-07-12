#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

contract_env="$(mktemp)"
invalid_env="$(mktemp)"
duplicate_env="$(mktemp)"
cleanup() {
  rm -f "$contract_env" "$invalid_env" "$duplicate_env"
}
trap cleanup EXIT

while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" != *=* || "$line" == \#* ]]; then
    printf '%s\n' "$line"
    continue
  fi
  key="${line%%=*}"
  value="${line#*=}"
  case "$key" in
    APP_ENV) value=production ;;
    APP_DOMAIN) value=bumpabestie.example.com ;;
    WWW_DOMAIN) value=www.bumpabestie.example.com ;;
    ADMIN_DOMAIN) value=admin.bumpabestie.example.com ;;
    RESEARCH_DOMAIN) value=research.bumpabestie.example.com ;;
    API_DOMAIN) value=api.bumpabestie.example.com ;;
    PUBLIC_ORIGIN) value=https://bumpabestie.example.com ;;
    ADMIN_ORIGIN) value=https://admin.bumpabestie.example.com ;;
    RESEARCH_ORIGIN) value=https://research.bumpabestie.example.com ;;
    API_ORIGIN) value=https://api.bumpabestie.example.com ;;
    CADDY_SITE_SCHEME) value=https ;;
    CADDY_BIND_ADDRESS) value=0.0.0.0 ;;
    CADDY_HTTP_PORT) value=80 ;;
    CADDY_HTTPS_PORT) value=443 ;;
    NEXT_PUBLIC_APP_URL) value=https://bumpabestie.example.com ;;
    NEXT_PUBLIC_API_BASE_URL) value=https://api.bumpabestie.example.com ;;
    TRUSTED_HOSTS) value=bumpabestie.example.com,www.bumpabestie.example.com,admin.bumpabestie.example.com,research.bumpabestie.example.com,api.bumpabestie.example.com,api ;;
    CORS_ALLOWED_ORIGINS) value=https://bumpabestie.example.com,https://admin.bumpabestie.example.com,https://research.bumpabestie.example.com ;;
    CORS_ORIGINS) value='["https://bumpabestie.example.com","https://admin.bumpabestie.example.com","https://research.bumpabestie.example.com"]' ;;
    JWT_SECRET) value=contract-jwt-secret-000000000000000000 ;;
    OTP_SECRET) value=contract-otp-secret-000000000000000000 ;;
    FIELD_ENCRYPTION_KEY) value=contract-field-key-000000000000000000 ;;
    INTERNAL_SERVICE_TOKEN) value=contract-internal-token-0000000000000 ;;
    COOKIE_SECRET) value=contract-cookie-secret-000000000000000 ;;
    SESSION_COOKIE_SECURE) value=true ;;
    POSTGRES_PASSWORD) value=contract-postgres-password-0000000000 ;;
    APP_POSTGRES_PASSWORD) value=contract-app-postgres-password-000000 ;;
    DATABASE_URL) value=postgresql+psycopg://bumpabestie_app:contract-app-postgres-password-000000@postgres:5432/bumpabestie ;;
    MIGRATION_DATABASE_URL) value=postgresql+psycopg://bumpabestie:contract-postgres-password-0000000000@postgres:5432/bumpabestie ;;
    SYNC_DATABASE_URL) value=postgresql://bumpabestie:contract-postgres-password-0000000000@postgres:5432/bumpabestie ;;
    WHATSAPP_BACKEND | AGENT_BACKEND | BUMPA_BACKEND) value=disabled ;;
    EXPOSE_LOCAL_OTP | SEED_DEMO_DATA | NEXT_PUBLIC_DEMO_MODE | ASYNC_RUNTIME_ENABLED) value=false ;;
    DEPLOY_REF) value=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
    IMAGE_TAG) value=sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
    INFRA_IMAGE_TAG) value=sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
    API_IMAGE) value=ghcr.io/makriman/bumpabestie-api@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc ;;
    WEB_IMAGE) value=ghcr.io/makriman/bumpabestie-web@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd ;;
    CADDY_IMAGE) value=ghcr.io/makriman/bumpabestie-caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee ;;
    POSTGRES_IMAGE) value=ghcr.io/makriman/bumpabestie-postgres@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff ;;
    BACKUP_IMAGE) value=ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab ;;
  esac
  printf '%s=%s\n' "$key" "$value"
done < .env.example > "$contract_env"
chmod 0600 "$contract_env"

./scripts/validate_env.sh "$contract_env" production
compose=(docker compose --env-file "$contract_env" -f compose.yaml -f compose.prod.yaml --profile tools --profile restore)
"${compose[@]}" config --quiet
rendered="$("${compose[@]}" config --format json)"

jq --exit-status '
  .services.caddy.image == "ghcr.io/makriman/bumpabestie-caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" and
  .services["caddy-init"].image == "ghcr.io/makriman/bumpabestie-caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" and
  .services["caddy-init"].build == null and .services["caddy-init"].network_mode == "none" and
  .services.caddy.cap_drop == ["ALL"] and .services.caddy.cap_add == ["NET_BIND_SERVICE"] and
  .services.caddy.security_opt == ["no-new-privileges:true"] and
  .services.postgres.image == "ghcr.io/makriman/bumpabestie-postgres@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff" and
  .services.postgres.stop_grace_period == "1m0s" and
  .services.backup.image == "ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab" and
  .services.restore.image == "ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab" and
  .services.caddy.build == null and .services.postgres.build == null and .services.backup.build == null and .services.restore.build == null and
  .services.backup.environment.BACKUP_IMAGE_REF == "ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab" and
  (.services.backup.networks | keys == ["data"]) and (.services.restore.networks | keys == ["data"]) and
  (.services.backup.cap_add | index("DAC_OVERRIDE") | not) and
  (.services.restore.cap_add | index("DAC_OVERRIDE") != null) and
  .services.api.environment.APP_ENV == "production" and
  .services.api.environment.WHATSAPP_BACKEND == "disabled" and
  .services.api.environment.AGENT_BACKEND == "disabled" and
  .services.api.environment.BUMPA_BACKEND == "disabled" and
  (.services.api.environment | has("MIGRATION_DATABASE_URL") | not)
' <<<"$rendered" >/dev/null

stop_line="$(grep -n -F "\"\${compose[@]}\" stop --timeout 60 caddy web api worker scheduler" scripts/deploy.sh | cut -d: -f1)"
backup_line="$(grep -n -E '^[[:space:]]+backup$' scripts/deploy.sh | cut -d: -f1)"
quiesced_line="$(grep -n -F 'writers_quiesced=1' scripts/deploy.sh | cut -d: -f1)"
role_init_line="$(grep -n -F '/docker-entrypoint-initdb.d/10-app-role.sh' scripts/deploy.sh | cut -d: -f1)"
migrate_line="$(grep -n -F "\"\${compose[@]}\" --profile tools run --rm migrate" scripts/deploy.sh | cut -d: -f1)"
if [[ -z "$stop_line" || -z "$backup_line" || -z "$quiesced_line" \
  || "$quiesced_line" -ge "$stop_line" || "$stop_line" -ge "$backup_line" ]]; then
  echo "Deployment does not quiesce application writers before the recovery-point backup" >&2
  exit 1
fi
if [[ -z "$role_init_line" || -z "$migrate_line" || "$role_init_line" -ge "$migrate_line" ]]; then
  echo "Deployment does not reconcile the application role before migrations" >&2
  exit 1
fi
if grep -Fq "export CADDY_IMAGE=\"\$previous_caddy_image\"" scripts/deploy.sh; then
  echo "Deployment attempts a backward infrastructure rollback" >&2
  exit 1
fi
grep -Fq -- '--exit-code-from caddy-init caddy-init' scripts/deploy.sh
grep -Fq "docker image inspect --format '{{json .RepoDigests}}'" scripts/deploy.sh

test "$(
  jq --raw-output '.services | to_entries[] | select(.value.ports != null) | .key' <<<"$rendered"
)" = caddy

awk '
  /^API_IMAGE=/ {
    print "API_IMAGE=ghcr.io/makriman/bumpabestie-api:latest"
    next
  }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted a mutable image reference" >&2
  exit 1
fi

cp "$contract_env" "$duplicate_env"
printf '%s\n' 'API_IMAGE=ghcr.io/makriman/bumpabestie-api:latest' >> "$duplicate_env"
chmod 0600 "$duplicate_env"
if ./scripts/validate_env.sh "$duplicate_env" production >/dev/null 2>&1; then
  echo "Production validation accepted a duplicate override" >&2
  exit 1
fi

echo "Production environment and immutable Compose contracts passed"
