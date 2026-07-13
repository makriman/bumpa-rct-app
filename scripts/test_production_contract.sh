#!/usr/bin/env bash
# This contract intentionally searches for literal shell source containing
# parameter expansions and command substitutions.
# shellcheck disable=SC2016
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

contract_env="$(mktemp)"
invalid_env="$(mktemp)"
duplicate_env="$(mktemp)"
live_env="$(mktemp)"
verification_env="$(mktemp)"
contract_secrets="$(mktemp -d)"
runtime_secret_volume="bumpabestie-runtime-secret-contract-$$"
backup_init_volume="bumpabestie-backup-init-contract-$$"
offsite_env="$(mktemp)"
offsite_hook="$(mktemp)"
offsite_marker="$(mktemp)"
cleanup() {
  rm -f "$contract_env" "$invalid_env" "$duplicate_env" "$live_env" \
    "$verification_env" "$offsite_env" "$offsite_hook" "$offsite_marker"
  rm -rf "$contract_secrets"
  docker volume rm --force "$runtime_secret_volume" >/dev/null 2>&1 || true
  docker volume rm --force "$backup_init_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

./scripts/test_release_boundary.sh
./scripts/test_promotion_state.sh
./scripts/test_promotion_coordinator.sh

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
    EXPOSE_LOCAL_OTP | SEED_DEMO_DATA | NEXT_PUBLIC_DEMO_MODE) value=false ;;
    ASYNC_RUNTIME_ENABLED) value=true ;;
    META_APP_ID) value=123456789012345 ;;
    META_BUSINESS_ID) value=234567890123456 ;;
    META_WABA_ID) value=345678901234567 ;;
    META_PHONE_NUMBER_ID) value=456789012345678 ;;
    META_PHONE_NUMBER) value=+2348000000000 ;;
    DEPLOY_REF) value=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
    IMAGE_TAG) value=sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
    INFRA_IMAGE_TAG) value=sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
    API_IMAGE) value=ghcr.io/makriman/bumpabestie-api@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc ;;
    WEB_IMAGE) value=ghcr.io/makriman/bumpabestie-web@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd ;;
    CADDY_IMAGE) value=ghcr.io/makriman/bumpabestie-caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee ;;
    POSTGRES_IMAGE) value=ghcr.io/makriman/bumpabestie-postgres@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff ;;
    BACKUP_IMAGE) value=ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab ;;
    HERMES_IMAGE) value=ghcr.io/makriman/bumpabestie-hermes@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc ;;
    SECRETS_DIR) value="$contract_secrets" ;;
  esac
  printf '%s=%s\n' "$key" "$value"
done < .env.example > "$contract_env"
chmod 0600 "$contract_env"
for secret_name in meta_app_secret meta_system_user_access_token meta_webhook_verify_token hermes_anthropic_api_key; do
  printf 'contract-secret-value-at-least-32-characters' > "$contract_secrets/$secret_name"
  chmod 0600 "$contract_secrets/$secret_name"
done
for secret_name in google_oauth_client_secret meta_ads_oauth_client_secret; do
  printf 'optional-oauth-secret-value-at-least-32-characters' > "$contract_secrets/$secret_name"
  chmod 0600 "$contract_secrets/$secret_name"
done

cat > "$offsite_hook" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
test -z "${JWT_SECRET:-}"
printf 'handoff-ran' > "$OFFSITE_TEST_MARKER"
EOF
chmod 0700 "$offsite_hook"
printf 'JWT_SECRET=must-not-be-exported\nOFFSITE_BACKUP_SCRIPT=%s\n' \
  "$offsite_hook" > "$offsite_env"
chmod 0600 "$offsite_env"
OFFSITE_TEST_MARKER="$offsite_marker" env -u OFFSITE_BACKUP_SCRIPT \
  ./scripts/offsite_backup.sh --env-file "$offsite_env"
test "$(cat "$offsite_marker")" = handoff-ran

# Compose file secrets are bind mounts and do not honor service uid/gid/mode on
# local engines. Exercise the one-shot copy boundary and prove the final API UID
# can read only the private runtime copies without weakening host permissions.
docker volume create "$runtime_secret_volume" >/dev/null
docker run --rm --network none --user 0:0 --read-only \
  --security-opt no-new-privileges:true \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --volume "$contract_secrets:/run/secrets:ro" \
  --volume "$contract_secrets:/run/optional-secrets:ro" \
  --volume "$runtime_secret_volume:/runtime-secrets" \
  --volume "$ROOT_DIR/scripts/init_runtime_secrets.sh:/usr/local/bin/init-runtime-secrets:ro" \
  python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df \
  /usr/local/bin/init-runtime-secrets
docker run --rm --network none --user 100:101 --read-only \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --volume "$runtime_secret_volume:/run/runtime-secrets:ro" \
  python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df \
  sh -eu -c '
    for name in meta_app_secret meta_system_user_access_token meta_webhook_verify_token google_oauth_client_secret meta_ads_oauth_client_secret; do
      test -r "/run/runtime-secrets/$name"
      test "$(stat -c %a "/run/runtime-secrets/$name")" = 400
    done
    test "$(find /run/runtime-secrets -mindepth 1 -maxdepth 1 -type f | wc -l)" = 5
  '

# Reproduce the ownership left by the former image entrypoint, then verify the
# capability-restricted initializer can migrate it without network access or
# DAC_OVERRIDE. A root process with no capabilities must subsequently be able
# to write the root-owned destination using only its owner permissions.
docker volume create "$backup_init_volume" >/dev/null
docker run --rm --network none --user 0:0 --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_READ_SEARCH --cap-add FOWNER \
  --volume "$backup_init_volume:/backups" \
  python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df \
  sh -eu -c '
    mkdir -p /backups/legacy/nested
    printf legacy > /backups/legacy/nested/manifest.json
    chown -R 70:70 /backups
    chmod 0700 /backups /backups/legacy /backups/legacy/nested
    chmod 0600 /backups/legacy/nested/manifest.json
  '
docker run --rm --network none --user 0:0 --read-only \
  --security-opt no-new-privileges:true \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_READ_SEARCH --cap-add FOWNER \
  --env BACKUP_DIR=/backups \
  --volume "$backup_init_volume:/backups" \
  --volume "$ROOT_DIR/scripts/init_backup_volume.sh:/usr/local/bin/init-backup-volume:ro" \
  python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df \
  /usr/local/bin/init-backup-volume
docker run --rm --network none --user 0:0 --read-only --cap-drop ALL \
  --volume "$backup_init_volume:/backups:ro" \
  python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df \
  sh -eu -c '
    test "$(stat -c "%u:%g:%a" /backups)" = 0:0:700
    test "$(stat -c "%u:%g:%a" /backups/legacy)" = 0:0:700
    test "$(stat -c "%u:%g" /backups/legacy/nested/manifest.json)" = 0:0
    test "$(cat /backups/legacy/nested/manifest.json)" = legacy
  '
docker run --rm --network none --user 0:0 --cap-drop ALL \
  --volume "$backup_init_volume:/backups" \
  python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df \
  sh -eu -c 'mkdir /backups/new-backup && test -d /backups/new-backup'

# Exercise the same util-linux flock implementation installed by the Ubuntu
# bootstrap. One process must exclude a zero-wait contender, release cleanly,
# and allow the next workflow to acquire the same host-external lock.
docker run --rm --network none \
  --volume "$ROOT_DIR:/workspace:ro" \
  ubuntu@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 \
  bash -ceu '
    lock_dir="$(mktemp -d)"
    ready="$lock_dir/ready"
    release="$lock_dir/release"
    mkfifo "$release"
    export BUMPABESTIE_MAINTENANCE_LOCK="$lock_dir/maintenance.lock"
    export BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS=5
    (
      source /workspace/scripts/maintenance_lock.sh
      acquire_maintenance_lock
      bash -ceu "source /workspace/scripts/maintenance_lock.sh; acquire_maintenance_lock"
      : > "$ready"
      read -r _ < "$release" || true
    ) &
    holder_pid=$!
    for _ in $(seq 1 100); do
      [[ -e "$ready" ]] && break
      sleep 0.05
    done
    [[ -e "$ready" ]]

    export BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS=0
    set +e
    (source /workspace/scripts/maintenance_lock.sh; acquire_maintenance_lock) 2>/dev/null
    contender_status=$?
    set -e
    [[ "$contender_status" -eq 75 ]]

    printf "release\n" > "$release"
    wait "$holder_pid"
    export BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS=1
    (source /workspace/scripts/maintenance_lock.sh; acquire_maintenance_lock)

    set +e
    (
      export BUMPABESTIE_MAINTENANCE_LOCK_FD=9
      source /workspace/scripts/maintenance_lock.sh
      acquire_maintenance_lock
    ) 2>/dev/null
    spoof_status=$?
    set -e
    [[ "$spoof_status" -eq 2 ]]
  '

./scripts/validate_env.sh "$contract_env" production
compose=(docker compose --env-file "$contract_env" -f compose.yaml -f compose.prod.yaml --profile async --profile tools --profile restore)
"${compose[@]}" config --quiet
rendered="$("${compose[@]}" config --format json)"

if ! jq --exit-status '
  .services.caddy.image == "ghcr.io/makriman/bumpabestie-caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" and
  .services["caddy-init"].image == "ghcr.io/makriman/bumpabestie-caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" and
  .services["caddy-init"].build == null and .services["caddy-init"].network_mode == "none" and
  .services.caddy.cap_drop == ["ALL"] and .services.caddy.cap_add == ["NET_BIND_SERVICE"] and
  .services.caddy.security_opt == ["no-new-privileges:true"] and
  .services.postgres.image == "ghcr.io/makriman/bumpabestie-postgres@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff" and
  .services.postgres.stop_grace_period == "1m0s" and
  .services.backup.image == "ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab" and
  .services["backup-data-init"].image == .services.backup.image and
  .services["backup-data-init"].build == null and
  .services["backup-data-init"].user == "0:0" and
  .services["backup-data-init"].entrypoint == ["/usr/local/bin/init-backup-volume"] and
  .services["backup-data-init"].network_mode == "none" and
  .services["backup-data-init"].read_only == true and
  .services["backup-data-init"].cap_drop == ["ALL"] and
  .services["backup-data-init"].cap_add == ["CHOWN", "DAC_READ_SEARCH", "FOWNER"] and
  ([.services["backup-data-init"].volumes[] | select(.target == "/backups" and .read_only != true)] | length == 1) and
  ([.services["backup-data-init"].volumes[] | select(.type == "bind" and .target == "/usr/local/bin/init-backup-volume" and .read_only == true)] | length == 1) and
  .services.backup.user == "0:0" and
  .services.restore.image == "ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab" and
  .services.restore.user == "0:0" and
  .services.restore.entrypoint == ["/usr/local/bin/restore.sh"] and
  .services.caddy.build == null and .services.postgres.build == null and .services.backup.build == null and .services.restore.build == null and
  .services.backup.environment.BACKUP_IMAGE_REF == "ghcr.io/makriman/bumpabestie-backup@sha256:abababababababababababababababababababababababababababababababab" and
  .services.backup.entrypoint == ["/usr/local/bin/backup.sh"] and
  .services.backup.read_only == true and
  .services.backup.tmpfs == ["/tmp:rw,nosuid,nodev,noexec"] and
  (.services.backup.networks | keys == ["data"]) and (.services.restore.networks | keys == ["data"]) and
  (.services.backup.cap_add | index("DAC_OVERRIDE") | not) and
  .services.backup.cap_add == ["DAC_READ_SEARCH"] and
  (.services.restore.cap_add | index("DAC_OVERRIDE") != null) and
  ([.services.backup.volumes[] | select(.target == "/source/exports" or (.target | startswith("/source/hermes-"))) | .read_only] | all) and
  ([.services.restore.volumes[] | select(.target == "/source/exports" or (.target | startswith("/source/hermes-"))) | .read_only] | any | not) and
  ([.services.restore.volumes[] | select(.target == "/backups") | .read_only] == [true]) and
  .services.restore.read_only == true and
  .services.restore.tmpfs == ["/tmp:rw,nosuid,nodev,noexec"] and
  .services.api.environment.APP_ENV == "production" and
  .services.api.environment.WHATSAPP_BACKEND == "disabled" and
  .services.api.environment.AGENT_BACKEND == "disabled" and
  .services.api.environment.BUMPA_BACKEND == "disabled" and
  (.services.api.command | index("--no-access-log") != null) and
  .services.worker.environment.ASYNC_RUNTIME_ENABLED == "true" and
  .services.scheduler.environment.ASYNC_RUNTIME_ENABLED == "true" and
  .services.worker.environment.PROACTIVE_INSIGHTS_ENABLED == "false" and
  .services.scheduler.environment.DAILY_INSIGHTS_ENABLED == "false" and
  .services.worker.environment.OPS_ALERTS_ENABLED == "false" and
  .services.worker.healthcheck.test == ["CMD", "python", "-m", "app.jobs.health", "worker"] and
  .services.scheduler.healthcheck.test == ["CMD", "python", "-m", "app.jobs.health", "scheduler"] and
  .services.worker.cap_drop == ["ALL"] and .services.scheduler.cap_drop == ["ALL"] and
  (.services.api.environment | has("MIGRATION_DATABASE_URL") | not)
' <<<"$rendered" >/dev/null; then
  jq '{
    caddy: (.services.caddy | {image, build, cap_drop, cap_add, security_opt}),
    caddy_init: (.services["caddy-init"] | {image, build, network_mode}),
    postgres: (.services.postgres | {image, build, stop_grace_period}),
    backup_data_init: (.services["backup-data-init"] | {image, build, user, entrypoint, network_mode, read_only, cap_drop, cap_add, volumes}),
    backup: (.services.backup | {image, build, user, networks, read_only, tmpfs, cap_add, environment}),
    restore: (.services.restore | {image, build, user, entrypoint, networks, read_only, tmpfs, cap_add}),
    api: (.services.api | {command, environment}),
    worker: (.services.worker | {environment, healthcheck, cap_drop}),
    scheduler: (.services.scheduler | {environment, healthcheck, cap_drop})
  }' <<<"$rendered" >&2
  exit 1
fi

if ! jq --exit-status '
  .services.hermes.image == "ghcr.io/makriman/bumpabestie-hermes@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" and
  .services.hermes.build == null and
  .services.hermes.cap_drop == ["ALL"] and .services.hermes.read_only == true and
  .services.hermes.stop_grace_period == "1m0s" and
  (.services.hermes.ports == null) and (.services.hermes.networks | keys == ["app", "egress"]) and
  .services.hermes.environment.ANTHROPIC_API_KEY_FILE == "/run/secrets/hermes_anthropic_api_key" and
  .services.hermes.environment.HERMES_CONTROL_PORT == "8699" and
  .services.hermes.environment.HERMES_PROFILE_ROOT == "/opt/data/profiles" and
  .services.hermes.environment.HERMES_STAGING_ROOT == "/staged/profiles" and
  .services.api.environment.HERMES_CONTROL_PORT == "8699" and
  .services.api.group_add == ["10000"] and
  (.services.hermes.healthcheck.test[1] | contains("bumpabestie-hermes-control")) and
  (.services.hermes.healthcheck.test[1] | contains("HERMES_CONTROL_PORT")) and
  (.services.hermes.environment | has("ANTHROPIC_API_KEY") | not) and
  (.services.hermes.secrets | length == 1) and
  .services.hermes.secrets[0].source == "hermes_anthropic_api_key" and
  .services.hermes.secrets[0].target == "hermes_anthropic_api_key" and
  .services.hermes.secrets[0].uid == "0" and .services.hermes.secrets[0].gid == "0" and
  .services.hermes.secrets[0].mode == "0400" and
  .services["hermes-import"].image == .services.hermes.image and
  .services["hermes-import"].network_mode == "none" and
  .services["hermes-import"].read_only == true and
  (.services["hermes-import"].networks == null) and
  (.services["hermes-import"].secrets == null) and
  .services["hermes-staging-init"].image == .services.api.image and
  .services["hermes-staging-init"].network_mode == "none" and
  (.services["hermes-staging-init"].command[0] | contains("chown -R 100:10000 /staged")) and
  (.services["hermes-staging-init"].command[0] | contains("chmod 2750")) and
  (.services["hermes-staging-init"].command[0] | contains("chmod 0640")) and
  (.services["hermes-staging-init"].command[0] | contains("unsafe filesystem entry")) and
  .services["app-secrets-init"].image == .services.api.image and
  .services["app-secrets-init"].network_mode == "none" and
  .services["app-secrets-init"].read_only == true and
  .services["app-secrets-init"].cap_drop == ["ALL"] and
  .services["app-secrets-init"].cap_add == ["CHOWN", "DAC_OVERRIDE", "FOWNER"] and
  (.services["app-secrets-init"].secrets | length == 3) and
  ([.services["app-secrets-init"].secrets[] | .source] | sort == ["meta_app_secret", "meta_system_user_access_token", "meta_webhook_verify_token"]) and
  .services.api.depends_on["hermes-staging-init"].condition == "service_completed_successfully" and
  .services.api.depends_on["app-secrets-init"].condition == "service_completed_successfully" and
  .services.worker.depends_on["app-secrets-init"].condition == "service_completed_successfully" and
  .services.scheduler.depends_on["app-secrets-init"].condition == "service_completed_successfully" and
  .services.api.environment.META_APP_SECRET_FILE == "/run/runtime-secrets/meta_app_secret" and
  .services.worker.environment.META_SYSTEM_USER_ACCESS_TOKEN_FILE == "/run/runtime-secrets/meta_system_user_access_token" and
  .services.worker.environment.OPS_ALERT_HMAC_SECRET_FILE == "/run/runtime-secrets/ops_alert_hmac_secret" and
  .services.scheduler.environment.OPS_ALERT_HMAC_SECRET_FILE == "/run/runtime-secrets/ops_alert_hmac_secret" and
  .services.api.environment.GOOGLE_OAUTH_CLIENT_SECRET_FILE == "/run/runtime-secrets/google_oauth_client_secret" and
  .services.worker.environment.META_ADS_OAUTH_CLIENT_SECRET_FILE == "/run/runtime-secrets/meta_ads_oauth_client_secret" and
  .services.scheduler.environment.GOOGLE_OAUTH_CLIENT_SECRET_FILE == "/run/runtime-secrets/google_oauth_client_secret" and
  (.services.api.secrets == null) and (.services.worker.secrets == null) and
  ([.services.api.volumes[] | select(.target == "/run/runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services.worker.volumes[] | select(.target == "/run/runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services.scheduler.volumes[] | select(.target == "/run/runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.target == "/runtime-secrets" and .read_only != true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .target == "/usr/local/bin/init-runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .source == "/dev/null" and .target == "/run/optional-secrets/ops_alert_hmac_secret" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .source == "/dev/null" and .target == "/run/optional-secrets/google_oauth_client_secret" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .source == "/dev/null" and .target == "/run/optional-secrets/meta_ads_oauth_client_secret" and .read_only == true)] | length == 1) and
  ([.services.api.volumes[] | select(.target == "/run/runtime-secrets") | .source] == [.services["app-secrets-init"].volumes[] | select(.target == "/runtime-secrets") | .source]) and
  (.services.scheduler.secrets == null) and (.services.migrate.secrets == null) and
  ([.services.backup.volumes[] | select(.target == "/source/hermes-runtime") | .source] == ["hermes_data"]) and
  ([.services.backup.volumes[] | select(.target == "/source/hermes-staging") | .source] == ["hermes_staging_data"]) and
  ([.services.hermes.volumes[] | select(.target == "/staged" and .read_only == true) | .source] == ["hermes_staging_data"]) and
  ([.services.api.volumes[] | select(.target == "/data/hermes") | .source] == ["hermes_staging_data"]) and
  ([.services.backup.volumes[] | select(.target | startswith("/source/hermes-"))] | length == 2) and
  ([.services[]?.volumes[]? | select(.source == "/var/run/docker.sock")] | length == 0)
' <<<"$rendered" >/dev/null; then
  jq '{
    hermes: (.services.hermes | {image, build, cap_drop, read_only, stop_grace_period, ports, networks, environment, secrets, volumes}),
    hermes_import: (.services["hermes-import"] | {image, network_mode, networks, read_only, secrets, volumes}),
    hermes_staging_init: (.services["hermes-staging-init"] | {image, command, network_mode, networks, read_only}),
    app_secrets_init: (.services["app-secrets-init"] | {image, network_mode, networks, read_only, cap_drop, cap_add, secrets, volumes}),
    api_meta_file: .services.api.environment.META_APP_SECRET_FILE,
    api_group_add: .services.api.group_add,
    worker_meta_file: .services.worker.environment.META_SYSTEM_USER_ACCESS_TOKEN_FILE,
    api_volumes: .services.api.volumes,
    worker_volumes: .services.worker.volumes,
    backup_volumes: .services.backup.volumes
  }' <<<"$rendered" >&2
  exit 1
fi

stop_line="$(grep -n -F "\"\${compose[@]}\" stop --timeout 60 caddy web api worker scheduler" scripts/deploy.sh | cut -d: -f1)"
backup_line="$(grep -n -E '^[[:space:]]+backup$' scripts/deploy.sh | cut -d: -f1)"
backup_init_line="$(grep -n -F 'run --rm --no-deps backup-data-init' scripts/deploy.sh | cut -d: -f1)"
quiesced_line="$(grep -n -F 'writers_quiesced=1' scripts/deploy.sh | cut -d: -f1)"
role_init_line="$(grep -n -F '/docker-entrypoint-initdb.d/10-app-role.sh' scripts/deploy.sh | cut -d: -f1)"
migrate_line="$(grep -n -F "\"\${compose[@]}\" --profile tools run --rm migrate" scripts/deploy.sh | cut -d: -f1)"
reconcile_line="$(grep -n -F 'ENV_FILE=.env.production ./scripts/reconcile_hermes_profiles.sh' scripts/deploy.sh | cut -d: -f1)"
# The following patterns intentionally match literal shell source.
# shellcheck disable=SC2016
application_start_line="$(grep -n -F '"${compose[@]}" --profile async up -d --wait' scripts/deploy.sh | tail -1 | cut -d: -f1)"
if [[ -z "$stop_line" || -z "$backup_init_line" || -z "$backup_line" || -z "$quiesced_line" \
  || "$quiesced_line" -ge "$stop_line" || "$stop_line" -ge "$backup_init_line" \
  || "$backup_init_line" -ge "$backup_line" ]]; then
  echo "Deployment does not quiesce application writers before the recovery-point backup" >&2
  exit 1
fi
deploy_lock_line="$(grep -n -F 'acquire_maintenance_lock' scripts/deploy.sh | head -1 | cut -d: -f1)"
deploy_env_line="$(grep -n -F 'if [[ ! -f .env.production ]]' scripts/deploy.sh | cut -d: -f1)"
release_helper_line="$(grep -n -F 'source "$ROOT_DIR/scripts/release_boundary.sh"' scripts/deploy.sh | cut -d: -f1)"
release_load_line="$(grep -n -F 'load_release_boundary .deployed-release.json' scripts/deploy.sh | cut -d: -f1)"
target_validate_line="$(grep -n -F './scripts/validate_env.sh .env.production production' scripts/deploy.sh | cut -d: -f1)"
scheduled_lock_line="$(grep -n -F 'acquire_maintenance_lock' scripts/scheduled_backup.sh | head -1 | cut -d: -f1)"
scheduled_env_line="$(grep -n -F "env_file=\"\${ENV_FILE:-.env.production}\"" scripts/scheduled_backup.sh | cut -d: -f1)"
if [[ -z "$deploy_lock_line" || -z "$deploy_env_line" || "$deploy_lock_line" -ge "$deploy_env_line" \
  || -z "$scheduled_lock_line" || -z "$scheduled_env_line" \
  || "$scheduled_lock_line" -ge "$scheduled_env_line" ]]; then
  echo "Maintenance workflows do not acquire their shared lock before reading mutable state" >&2
  exit 1
fi
if [[ -z "$release_helper_line" || -z "$release_load_line" || -z "$target_validate_line" \
  || "$release_helper_line" -ge "$release_load_line" \
  || "$release_load_line" -ge "$target_validate_line" ]]; then
  echo "Deployment does not load the previous release boundary before target preflight" >&2
  exit 1
fi
grep -Fq 'trap early_failure_restore EXIT' scripts/deploy.sh
grep -Fq 'restore_previous_release_pointers' scripts/deploy.sh
grep -Fq 'for service in api worker scheduler web hermes caddy postgres redis' scripts/deploy.sh
grep -Fq 'actual_image="$(running_image "$service")"' scripts/deploy.sh
grep -Fq 'automatic_rollback_available=1' scripts/deploy.sh
grep -Fq 'elif ((deployment_started && automatic_rollback_available))' scripts/deploy.sh
grep -Fq 'SMOKE_ORIGIN_ADDRESS=127.0.0.1' scripts/deploy.sh
grep -Fq 'SMOKE_OVERALL_TIMEOUT_SECONDS=180' scripts/deploy.sh
grep -Fq 'if ! SMOKE_SCHEME=https' scripts/deploy.sh
grep -Fq 'SMOKE_ORIGIN_ADDRESS=' scripts/deploy.sh
grep -Fq 'SMOKE_OVERALL_TIMEOUT_SECONDS=60' scripts/deploy.sh
test "$(grep -Ec '^[[:space:]]*run_production_smoke$' scripts/deploy.sh)" = 3
if grep -Fq 'SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh' scripts/deploy.sh; then
  echo "Deployment bypasses the bounded direct-origin and edge smoke gates" >&2
  exit 1
fi
grep -Fq '"$previous_revision" "$previous_image_tag" "$target_infra_image_tag"' scripts/deploy.sh
grep -Fq 'Application rollback succeeded and its forward-infrastructure release boundary was persisted.' scripts/deploy.sh
grep -Fq '/usr/local/sbin/bumpabestie-promote' docs/deployment.md
grep -Fq 'Never invoke `scripts/promote_release.sh` or' docs/runbook.md
test -x infra/bin/bumpabestie-promote
test -x scripts/promote_release.sh
promotion_lock_line="$(grep -n -F 'acquire_maintenance_lock' scripts/promote_release.sh | head -1 | cut -d: -f1)"
promotion_record_line="$(grep -n -F 'load_release_boundary .deployed-release.json' scripts/promote_release.sh | cut -d: -f1)"
promotion_checkout_line="$(grep -n -F 'git checkout --detach "$revision"' scripts/promote_release.sh | cut -d: -f1)"
promotion_pointer_line="$(grep -n -F 'rewrite_release_pointers .env.production' scripts/promote_release.sh | tail -1 | cut -d: -f1)"
promotion_exec_line="$(grep -n -F '"$ROOT_DIR/scripts/deploy.sh"' scripts/promote_release.sh | tail -1 | cut -d: -f1)"
if [[ -z "$promotion_lock_line" || -z "$promotion_record_line" \
  || -z "$promotion_checkout_line" || -z "$promotion_pointer_line" \
  || -z "$promotion_exec_line" \
  || "$promotion_lock_line" -ge "$promotion_record_line" \
  || "$promotion_record_line" -ge "$promotion_checkout_line" \
  || "$promotion_checkout_line" -ge "$promotion_pointer_line" \
  || "$promotion_pointer_line" -ge "$promotion_exec_line" ]]; then
  echo "Release promotion is not serialized across record load, checkout, pointer selection and deploy exec" >&2
  exit 1
fi
grep -Fq 'BUMPABESTIE_PREVIOUS_CHECKOUT="$original_checkout"' scripts/promote_release.sh
grep -Fq 'BUMPABESTIE_PROMOTION_STATE_FILE="$promotion_state_file"' scripts/promote_release.sh
grep -Fq 'BUMPABESTIE_STABLE_COORDINATOR' scripts/promote_release.sh
grep -Fq 'git -C "$repo" show "$revision:scripts/$helper"' infra/bin/bumpabestie-promote
grep -Fq 'BUMPABESTIE_COORDINATOR_JOURNAL="$journal"' infra/bin/bumpabestie-promote
grep -Fq 'install -m 0755 -o root -g root' scripts/bootstrap_server.sh
grep -Fq '/usr/local/sbin/bumpabestie-promote' Makefile
if grep -Fq './scripts/deploy.sh' Makefile; then
  echo "Makefile bypasses the stable promotion coordinator" >&2
  exit 1
fi
grep -Fq 'restore_previous_checkout' scripts/deploy.sh
grep -Fq '.operations_revision // .revision' scripts/release_boundary.sh
grep -Fq 'Direct deployment is forbidden; use the guarded release promotion entrypoint' scripts/deploy.sh
grep -Fq 'if [[ "$(git rev-parse HEAD)" != "$deploy_commit" ]]' scripts/deploy.sh
if grep -Eq '^git (fetch|checkout)' scripts/deploy.sh; then
  echo "Deploy script mutates its checkout instead of requiring the guarded promotion handoff" >&2
  exit 1
fi
grep -Fq 'write_promotion_state "$promotion_state_file" FORWARD_BOUNDARY' scripts/deploy.sh
grep -Fq 'write_promotion_state "$promotion_state_file" COMMITTED' scripts/deploy.sh
grep -Fq 'assert_maintenance_clear' scripts/scheduled_backup.sh
hybrid_pointer_line="$(grep -n -F 'if ((rollback_result == 0)) && rewrite_release_pointers .env.production' scripts/deploy.sh | cut -d: -f1)"
hybrid_metadata_line="$(grep -n -F '&& persist_release_metadata' scripts/deploy.sh | cut -d: -f1)"
if [[ -z "$hybrid_pointer_line" || -z "$hybrid_metadata_line" \
  || "$hybrid_pointer_line" -ge "$hybrid_metadata_line" ]]; then
  echo "Hybrid rollback does not persist actual-safe environment pointers before metadata" >&2
  exit 1
fi
if [[ -z "$role_init_line" || -z "$migrate_line" || "$role_init_line" -ge "$migrate_line" ]]; then
  echo "Deployment does not reconcile the application role before migrations" >&2
  exit 1
fi
if [[ -z "$reconcile_line" || -z "$application_start_line" \
  || "$migrate_line" -ge "$reconcile_line" || "$reconcile_line" -ge "$application_start_line" ]]; then
  echo "Deployment does not reconcile staged Hermes profiles between migration and application startup" >&2
  exit 1
fi
if grep -Fq "export CADDY_IMAGE=\"\$previous_caddy_image\"" scripts/deploy.sh; then
  echo "Deployment attempts a backward infrastructure rollback" >&2
  exit 1
fi
grep -Fq -- '--exit-code-from caddy-init caddy-init' scripts/deploy.sh
grep -Fq 'exclude http.log.access' infra/caddy/Caddyfile
grep -Fq 'format filter {' infra/caddy/Caddyfile
grep -Fq 'request>uri query {' infra/caddy/Caddyfile
grep -Fq 'replace hub.verify_token REDACTED' infra/caddy/Caddyfile
grep -Fq 'wrap json' infra/caddy/Caddyfile
grep -Fq 'health_uri /health/live' infra/caddy/Caddyfile
if grep -Fq 'health_uri /health/ready' infra/caddy/Caddyfile; then
  echo "Caddy must not remove durable ingress solely because an async dependency is degraded" >&2
  exit 1
fi
grep -Fq "docker image inspect --format '{{json .RepoDigests}}'" scripts/deploy.sh
grep -Fq "\"\${compose[@]}\" --profile tools run --rm --no-deps" scripts/deploy.sh
grep -Fq 'ExecStart=/opt/bumpabestie/scripts/scheduled_backup.sh' infra/systemd/bumpabestie-backup.service
grep -Fq 'ExecStart=/usr/bin/python3 /opt/bumpabestie/scripts/check_disk_usage.py' infra/systemd/bumpabestie-disk-usage.service
grep -Fq 'Environment=BUMPABESTIE_DISK_THRESHOLD_PERCENT=85' infra/systemd/bumpabestie-disk-usage.service
grep -Fq 'Environment=BUMPABESTIE_DISK_PATHS=/' infra/systemd/bumpabestie-disk-usage.service
grep -Fq 'OnUnitActiveSec=5min' infra/systemd/bumpabestie-disk-usage.timer
grep -Fq 'Unit=bumpabestie-disk-usage.service' infra/systemd/bumpabestie-disk-usage.timer
# These assertions intentionally match literal shell source.
# shellcheck disable=SC2016
grep -Fq 'systemctl is-enabled --quiet "$timer_name"' scripts/deploy.sh
# shellcheck disable=SC2016
grep -Fq 'systemctl is-active --quiet "$timer_name"' scripts/deploy.sh
if grep -Fq 'EnvironmentFile=' infra/systemd/bumpabestie-disk-usage.service; then
  echo "Disk usage unit must not import the application environment" >&2
  exit 1
fi
# Match literal shell source rather than expanding this test process's array.
# shellcheck disable=SC2016
grep -Fq 'stop --timeout 60 "${running_services[@]}"' scripts/scheduled_backup.sh
grep -Fq 'writer_services=(api worker scheduler hermes)' scripts/scheduled_backup.sh
if grep -Eq 'writer_services=\([^)]*(caddy|web)' scripts/scheduled_backup.sh; then
  echo "Scheduled backup must keep the non-writing public edge services online" >&2
  exit 1
fi
grep -Fq 'run --rm --no-deps backup' scripts/scheduled_backup.sh
scheduled_backup_init_line="$(grep -n -F 'run --rm --no-deps backup-data-init' scripts/scheduled_backup.sh | cut -d: -f1)"
scheduled_backup_line="$(grep -n -F 'run --rm --no-deps backup' scripts/scheduled_backup.sh | tail -1 | cut -d: -f1)"
if [[ -z "$scheduled_backup_init_line" || -z "$scheduled_backup_line" \
  || "$scheduled_backup_init_line" -ge "$scheduled_backup_line" ]]; then
  echo "Scheduled backup does not initialize its destination before backup execution" >&2
  exit 1
fi
grep -Fq 'ExecStartPost=/opt/bumpabestie/scripts/offsite_backup.sh --env-file /opt/bumpabestie/.env.production' infra/systemd/bumpabestie-backup.service
if grep -Fq 'EnvironmentFile=' infra/systemd/bumpabestie-backup.service; then
  echo "Backup unit must not export the application environment to the off-host hook" >&2
  exit 1
fi
for hardening_directive in \
  'StateDirectory=bumpabestie' \
  'StateDirectoryMode=0700' \
  'UMask=0077' \
  'NoNewPrivileges=yes' \
  'PrivateDevices=yes' \
  'PrivateTmp=yes' \
  'RemoveIPC=yes' \
  'ProtectHome=read-only' \
  'ProtectSystem=strict' \
  'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' \
  'RestrictNamespaces=yes' \
  'RestrictSUIDSGID=yes' \
  'SystemCallArchitectures=native' \
  'CapabilityBoundingSet='; do
  grep -Fxq "$hardening_directive" infra/systemd/bumpabestie-backup.service
done
for hardening_directive in \
  'UMask=0077' \
  'NoNewPrivileges=yes' \
  'PrivateDevices=yes' \
  'PrivateTmp=yes' \
  'RemoveIPC=yes' \
  'ProtectHome=yes' \
  'ProtectSystem=strict' \
  'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' \
  'RestrictNamespaces=yes' \
  'RestrictSUIDSGID=yes' \
  'SystemCallArchitectures=native' \
  'CapabilityBoundingSet='; do
  grep -Fxq "$hardening_directive" infra/systemd/bumpabestie-disk-usage.service
done
grep -Fq 'util-linux' scripts/bootstrap_server.sh
grep -Eq 'apt-get install -y .*python3' scripts/bootstrap_server.sh
grep -Fq 'bumpabestie-disk-usage.timer' scripts/bootstrap_server.sh
grep -Fq 'install -d -m 0700 -o bumpabestie -g bumpabestie /var/lib/bumpabestie' scripts/bootstrap_server.sh
grep -Fq 'BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock' scripts/maintenance_lock.sh
grep -Fq 'BUMPABESTIE_MAINTENANCE_LOCK_WAIT_SECONDS:-900' scripts/maintenance_lock.sh
grep -Fq 'BUMPABESTIE_MAINTENANCE_LOCK_FD:-' scripts/maintenance_lock.sh
grep -Fq -- '--profile async up -d --wait' scripts/deploy.sh
# Match literal deploy-script variables.
# shellcheck disable=SC2016
grep -Fq 'cmp --silent "$repository_unit" "$installed_unit"' scripts/deploy.sh
# These assertions intentionally match literal shell source.
# shellcheck disable=SC2016
grep -Fq 'verify_image_revision "$HERMES_IMAGE" "$deploy_commit" hermes' scripts/deploy.sh
# shellcheck disable=SC2016
grep -Fq 'previous_hermes_image="$(running_image hermes)"' scripts/deploy.sh
# shellcheck disable=SC2016
grep -Fq 'export HERMES_IMAGE="$previous_hermes_image"' scripts/deploy.sh
if grep -Fq 'rm -f worker scheduler 2>/dev/null || true' scripts/deploy.sh; then
  # The rollback-only cleanup is allowed; the forward deployment must not remove them.
  test "$(grep -Fc 'rm -f worker scheduler 2>/dev/null || true' scripts/deploy.sh)" = 1
fi

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

awk '
  /^ASYNC_RUNTIME_ENABLED=/ { print "ASYNC_RUNTIME_ENABLED=false"; next }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted a disabled async runtime" >&2
  exit 1
fi

awk '
  /^ASYNC_HEARTBEAT_TTL_SECONDS=/ { print "ASYNC_HEARTBEAT_TTL_SECONDS=5"; next }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted an unsafe async heartbeat TTL" >&2
  exit 1
fi

awk '
  /^DAILY_INSIGHTS_ENABLED=/ { print "DAILY_INSIGHTS_ENABLED=true"; next }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted an insight cadence without its master gate" >&2
  exit 1
fi

awk '
  /^OPS_ALERTS_ENABLED=/ { print "OPS_ALERTS_ENABLED=true"; next }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted enabled alerts without endpoint credentials" >&2
  exit 1
fi

awk '
  /^MCP_GOOGLE_OAUTH_ENABLED=/ { print "MCP_GOOGLE_OAUTH_ENABLED=true"; next }
  /^GOOGLE_OAUTH_CLIENT_ID=/ { print "GOOGLE_OAUTH_CLIENT_ID=contract-client.apps.example.test"; next }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted Google OAuth without a private host secret" >&2
  exit 1
fi

awk '
  /^MCP_META_ADS_OAUTH_ENABLED=/ { print "MCP_META_ADS_OAUTH_ENABLED=true"; next }
  /^META_ADS_OAUTH_CLIENT_ID=/ { print "META_ADS_OAUTH_CLIENT_ID=123456789012345"; next }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted Meta Ads OAuth without a private host secret" >&2
  exit 1
fi

cp "$contract_env" "$duplicate_env"
printf '%s\n' 'API_IMAGE=ghcr.io/makriman/bumpabestie-api:latest' >> "$duplicate_env"
chmod 0600 "$duplicate_env"
if ./scripts/validate_env.sh "$duplicate_env" production >/dev/null 2>&1; then
  echo "Production validation accepted a duplicate override" >&2
  exit 1
fi

awk '
  /^WHATSAPP_BACKEND=/ { print "WHATSAPP_BACKEND=meta"; next }
  /^AGENT_BACKEND=/ { print "AGENT_BACKEND=hermes"; next }
  /^BUMPA_BACKEND=/ { print "BUMPA_BACKEND=bumpa"; next }
  { print }
' "$contract_env" > "$live_env"
chmod 0600 "$live_env"
./scripts/validate_env.sh "$live_env" production >/dev/null

awk '
  /^META_TEST_SENDER_VERIFICATION_MODE=/ {
    print "META_TEST_SENDER_VERIFICATION_MODE=inbound_replies_only"; next
  }
  /^META_TEST_SENDER_WABA_ID=/ { print "META_TEST_SENDER_WABA_ID=567890123456789"; next }
  /^META_TEST_SENDER_PHONE_NUMBER_ID=/ {
    print "META_TEST_SENDER_PHONE_NUMBER_ID=678901234567890"; next
  }
  /^META_TEST_SENDER_DISPLAY_PHONE_E164=/ {
    print "META_TEST_SENDER_DISPLAY_PHONE_E164=+15550102030"; next
  }
  { print }
' "$live_env" > "$verification_env"
chmod 0600 "$verification_env"
./scripts/validate_env.sh "$verification_env" production >/dev/null

awk '
  /^META_TEST_SENDER_WABA_ID=/ { print "META_TEST_SENDER_WABA_ID="; next }
  { print }
' "$verification_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted an incomplete Meta test-sender mapping" >&2
  exit 1
fi

awk '
  /^HERMES_PROFILE_PORT_START=/ { print "HERMES_PROFILE_PORT_START=9000"; next }
  /^HERMES_PROFILE_PORT_END=/ { print "HERMES_PROFILE_PORT_END=8999"; next }
  { print }
' "$live_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted an inverted Hermes profile port range" >&2
  exit 1
fi

awk '
  /^HERMES_CONTROL_PORT=/ { print "HERMES_CONTROL_PORT=8700"; next }
  { print }
' "$live_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted a Hermes control/profile port collision" >&2
  exit 1
fi

echo "Production environment and immutable Compose contracts passed"
