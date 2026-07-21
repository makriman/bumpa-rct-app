#!/usr/bin/env bash
# This contract intentionally searches for literal shell source containing
# parameter expansions and command substitutions.
# shellcheck disable=SC2016
set -Eeuo pipefail

report_contract_error() {
  local exit_status="$?"
  printf 'Production contract failed at line %s\n' "${BASH_LINENO[0]}" >&2
  return "$exit_status"
}
trap report_contract_error ERR

require_single_line_number() {
  local selector_name="$1"
  local selector_value="$2"
  if [[ ! "$selector_value" =~ ^[0-9]+$ ]]; then
    echo "Production contract line selector is missing or ambiguous: $selector_name" >&2
    exit 1
  fi
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

contract_env="$(mktemp)"
invalid_env="$(mktemp)"
duplicate_env="$(mktemp)"
live_env="$(mktemp)"
verification_env="$(mktemp)"
temporary_verification_env="$(mktemp)"
disabled_auth_env="$(mktemp)"
whatsapp_auth_env="$(mktemp)"
versioned_auth_env="$(mktemp)"
contract_secrets="$(mktemp -d)"
hermes_health_probe="$(mktemp -d)"
auth_secret_contract_name="bumpabestie-auth-secret"
auth_secret_contract_token="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
auth_secret_contract_root="/var/lib/$auth_secret_contract_name"
auth_secret_contract_dir="$auth_secret_contract_root/temporary-web-pin-verifiers"
auth_secret_contract_file="$auth_secret_contract_dir/$auth_secret_contract_token"
auth_secret_runtime_image='python@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df'
auth_secret_init_volume="bumpabestie-auth-secret-init-contract-$$"
auth_secret_malformed_volume="$auth_secret_init_volume-malformed"
runtime_secret_volume="bumpabestie-runtime-secret-contract-$$"
backup_init_volume="bumpabestie-backup-init-contract-$$"
offsite_env="$(mktemp)"
offsite_hook="$(mktemp)"
offsite_marker="$(mktemp)"
auth_secret_fixture_created=0
auth_helper_contract_user_created=0
auth_helper_contract_installed=0
cleanup() {
  if ((auth_helper_contract_installed)); then
    sudo rm -f \
      /etc/sudoers.d/bumpabestie-temporary-auth-secret \
      /usr/local/sbin/bumpabestie-validate-temporary-auth-secret \
      >/dev/null 2>&1 || true
  fi
  if ((auth_helper_contract_user_created)); then
    sudo userdel --remove bumpabestie >/dev/null 2>&1 || true
  fi
  if ((auth_secret_fixture_created)); then
    docker run --rm --network none --read-only \
      --cap-drop ALL --cap-add DAC_OVERRIDE \
      --security-opt no-new-privileges:true \
      --mount type=bind,source=/var/lib,target=/host-var-lib \
      --entrypoint sh "$auth_secret_runtime_image" \
      -eu -c '
        verifier="/host-var-lib/$1/temporary-web-pin-verifiers/$2"
        rm -f -- "$verifier"
        rmdir -- "/host-var-lib/$1/temporary-web-pin-verifiers"
        rmdir -- "/host-var-lib/$1"
      ' cleanup "$auth_secret_contract_name" "$auth_secret_contract_token" \
      >/dev/null 2>&1 || true
  fi
  rm -f "$contract_env" "$invalid_env" "$duplicate_env" "$live_env" \
    "$verification_env" "$temporary_verification_env" "$disabled_auth_env" "$whatsapp_auth_env" \
    "$versioned_auth_env" \
    "$offsite_env" "$offsite_hook" "$offsite_marker"
  rm -rf "$contract_secrets" "$hermes_health_probe"
  docker volume rm --force "$auth_secret_init_volume" >/dev/null 2>&1 || true
  docker volume rm --force "$auth_secret_malformed_volume" >/dev/null 2>&1 || true
  docker volume rm --force "$runtime_secret_volume" >/dev/null 2>&1 || true
  docker volume rm --force "$backup_init_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

./scripts/test_release_boundary.sh
./scripts/test_promotion_state.sh
./scripts/test_promotion_coordinator.sh
./scripts/test_rollback_containment.sh
./scripts/test_docker_build_context.sh
./scripts/test_temporary_login_pin_rotation.sh

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
    AUTH_LOGIN_MODE) value=temporary_static_pin ;;
    TEMPORARY_WEB_PIN_VERIFIER_FILE) value=/run/auth-secret/temporary_web_pin_verifier ;;
    TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST) value=/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier ;;
    TEMPORARY_WEB_PIN_EXPIRES_AT) value=2099-01-01T00:00:00Z ;;
    FIELD_ENCRYPTION_KEY) value=contract-field-key-000000000000000000 ;;
    RESEARCH_PSEUDONYM_KEY) value=contract-research-pseudonym-key-000000000 ;;
    ONBOARDING_INTEGRITY_KEY) value=contract-onboarding-integrity-key-0000000 ;;
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

render_non_temporary_auth_env() {
  local auth_mode="$1"
  local whatsapp_mode="$2"
  local destination="$3"
  awk -F= -v auth_mode="$auth_mode" -v whatsapp_mode="$whatsapp_mode" '
    $1 == "AUTH_LOGIN_MODE" { print "AUTH_LOGIN_MODE=" auth_mode; next }
    $1 == "WHATSAPP_BACKEND" { print "WHATSAPP_BACKEND=" whatsapp_mode; next }
    $1 == "TEMPORARY_WEB_PIN_VERIFIER" ||
    $1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE" ||
    $1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" ||
    $1 == "TEMPORARY_WEB_PIN_EXPIRES_AT" { print $1 "="; next }
    { print }
  ' "$contract_env" > "$destination"
  chmod 0600 "$destination"
}
render_non_temporary_auth_env disabled disabled "$disabled_auth_env"
render_non_temporary_auth_env whatsapp_otp meta "$whatsapp_auth_env"
./scripts/validate_env.sh "$disabled_auth_env" production
./scripts/validate_env.sh "$whatsapp_auth_env" production
# Compose 2.38 validates this field but omits an explicit false value from its
# normalized JSON, so retain a source assertion alongside the rendered checks.
if [[ "$(grep -Fc 'create_host_path: false' compose.prod.yaml)" != 1 ]]; then
  echo "Production Compose must explicitly disable verifier bind-path creation" >&2
  exit 1
fi

for non_temporary_env in "$disabled_auth_env" "$whatsapp_auth_env"; do
  non_temporary_rendered="$(
    docker compose --env-file "$non_temporary_env" \
      -f compose.yaml -f compose.prod.yaml config --format json
  )"
  jq --exit-status '
    .services["auth-secret-init"].environment.AUTH_LOGIN_MODE != "temporary_static_pin" and
    ([.services["auth-secret-init"].volumes[] |
      select(
        .type == "bind" and
        .source == "/dev/null" and
        .target == "/run/host-auth-secret/temporary_web_pin_verifier" and
        .read_only == true and
        (.bind.create_host_path // false) == false
      )] | length == 1) and
    .services.api.environment.TEMPORARY_WEB_PIN_VERIFIER == "" and
    .services.api.environment.TEMPORARY_WEB_PIN_VERIFIER_FILE == "" and
    .services.api.environment.TEMPORARY_WEB_PIN_EXPIRES_AT == "" and
    (.secrets | has("temporary_web_pin_verifier") | not)
  ' <<<"$non_temporary_rendered" >/dev/null
done

docker pull "$auth_secret_runtime_image" >/dev/null
docker volume create "$auth_secret_init_volume" >/dev/null
for non_temporary_mode in disabled whatsapp_otp; do
  docker run --rm --pull never \
    --network none \
    --read-only \
    --user 0:0 \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --env "AUTH_LOGIN_MODE=$non_temporary_mode" \
    --env TEMPORARY_WEB_PIN_VERIFIER_SOURCE=/run/host-auth-secret/temporary_web_pin_verifier \
    --volume "$auth_secret_init_volume:/runtime-auth-secret" \
    --volume "$ROOT_DIR/scripts/init_auth_secret.sh:/usr/local/bin/init-auth-secret:ro" \
    --mount type=bind,source=/dev/null,target=/run/host-auth-secret/temporary_web_pin_verifier,readonly \
    --entrypoint /usr/local/bin/init-auth-secret \
    "$auth_secret_runtime_image"
  docker run --rm --network none --read-only \
    --cap-drop ALL \
    --cap-add DAC_READ_SEARCH \
    --security-opt no-new-privileges:true \
    --volume "$auth_secret_init_volume:/runtime-auth-secret:ro" \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      test "$(stat -c %u:%g /runtime-auth-secret)" = 100:101
      test "$(stat -c %a /runtime-auth-secret)" = 500
      test -z "$(find /runtime-auth-secret -mindepth 1 -maxdepth 1 -print -quit)"
    '
done
if docker run --rm --pull never \
    --network none \
    --read-only \
    --user 0:0 \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --env AUTH_LOGIN_MODE=invalid \
    --volume "$auth_secret_init_volume:/runtime-auth-secret" \
    --volume "$ROOT_DIR/scripts/init_auth_secret.sh:/usr/local/bin/init-auth-secret:ro" \
    --mount type=bind,source=/dev/null,target=/run/host-auth-secret/temporary_web_pin_verifier,readonly \
    --entrypoint /usr/local/bin/init-auth-secret \
    "$auth_secret_runtime_image" >/dev/null 2>&1; then
  echo "Auth-secret initializer accepted an invalid login mode" >&2
  exit 1
fi
for secret_name in meta_app_secret meta_system_user_access_token meta_webhook_verify_token hermes_anthropic_api_key; do
  printf 'contract-secret-value-at-least-32-characters' > "$contract_secrets/$secret_name"
  chmod 0600 "$contract_secrets/$secret_name"
done
auth_secret_fixture_supported=0
auth_secret_validator_prefix=()
if docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --mount type=bind,source=/var/lib,target=/host-var-lib \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      complete=0
      root="/host-var-lib/$1"
      versions="$root/temporary-web-pin-verifiers"
      verifier="$versions/$2"
      cleanup_partial() {
        if test "$complete" != 1; then
          rm -f -- "$verifier"
          rmdir -- "$versions" 2>/dev/null || true
          rmdir -- "$root" 2>/dev/null || true
        fi
      }
      trap cleanup_partial EXIT
      test ! -e "$root"
      test ! -L "$root"
      mkdir -m 0700 "$root"
      chown 0:0 "$root"
      mkdir -m 0700 "$versions"
      chown 0:0 "$versions"
      test ! -e "$verifier"
      test ! -L "$verifier"
      (umask 077; set -C; printf "%064d\n" 0 > "$verifier")
      chown 0:0 "$verifier"
      chmod 0600 "$verifier"
      test "$(stat -c %u:%g:%a:%h "$verifier")" = 0:0:600:1
      complete=1
    ' fixture "$auth_secret_contract_name" "$auth_secret_contract_token" >/dev/null 2>&1; then
  auth_secret_fixture_created=1
  auth_secret_fixture_supported=1
fi
if ((auth_secret_fixture_supported && EUID != 0)) \
  && [[ "${CI:-}" == "true" ]]; then
  command -v sudo >/dev/null 2>&1
  command -v visudo >/dev/null 2>&1
  getent group docker >/dev/null
  if id -u bumpabestie >/dev/null 2>&1; then
    echo "Installed-helper contract requires an isolated bumpabestie account" >&2
    exit 1
  fi
  sudo test ! -e /usr/local/sbin/bumpabestie-validate-temporary-auth-secret
  sudo test ! -e /etc/sudoers.d/bumpabestie-temporary-auth-secret
  sudo useradd --create-home --shell /bin/bash --groups docker bumpabestie
  auth_helper_contract_user_created=1
  if sudo -u bumpabestie -H sudo -n \
      /usr/local/sbin/bumpabestie-validate-temporary-auth-secret \
      disabled '' "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary-auth helper unexpectedly ran before installation" >&2
    exit 1
  fi
  sudo install -m 0755 -o root -g root \
    scripts/validate_temporary_auth_secret.sh \
    /usr/local/sbin/bumpabestie-validate-temporary-auth-secret
  auth_helper_contract_installed=1
  if sudo -u bumpabestie -H sudo -n \
      /usr/local/sbin/bumpabestie-validate-temporary-auth-secret \
      disabled '' "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary-auth helper ran without its narrow sudoers policy" >&2
    exit 1
  fi
  sudo visudo -cf infra/sudoers/bumpabestie-temporary-auth-secret >/dev/null
  sudo install -m 0440 -o root -g root \
    infra/sudoers/bumpabestie-temporary-auth-secret \
    /etc/sudoers.d/bumpabestie-temporary-auth-secret
  sudo visudo -cf /etc/sudoers.d/bumpabestie-temporary-auth-secret >/dev/null
  test "$(sudo stat -c '%U:%G:%a' \
    /usr/local/sbin/bumpabestie-validate-temporary-auth-secret)" = root:root:755
  test "$(sudo stat -c '%U:%G:%a' \
    /etc/sudoers.d/bumpabestie-temporary-auth-secret)" = root:root:440
  if sudo -u bumpabestie -H sudo -n \
      "$ROOT_DIR/scripts/validate_temporary_auth_secret.sh" \
      disabled '' "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Sudoers policy elevated the mutable checkout validator" >&2
    exit 1
  fi
  sudo -u bumpabestie -H sudo -n \
    /usr/local/sbin/bumpabestie-validate-temporary-auth-secret \
    disabled '' "$auth_secret_runtime_image"
  sudo -u bumpabestie -H sudo -n \
    /usr/local/sbin/bumpabestie-validate-temporary-auth-secret \
    temporary_static_pin "$auth_secret_contract_file" \
    "$auth_secret_runtime_image"
fi
if ((auth_secret_fixture_supported && EUID != 0)); then
  if ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Mutable checkout validator crossed the root-only verifier boundary" >&2
    exit 1
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    # The installed-policy harness above proves the production crossing. These
    # calls exercise the helper implementation's root-only negative matrix.
    auth_secret_validator_prefix=(sudo -n)
  else
    auth_secret_fixture_supported=0
  fi
fi
if ((auth_secret_fixture_supported)); then
  "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
    disabled '' "$auth_secret_runtime_image"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      disabled "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Disabled authentication accepted a verifier host path" >&2
    exit 1
  fi
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      invalid '' "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted an invalid login mode" >&2
    exit 1
  fi
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin /var/lib/../temporary_web_pin_verifier \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a non-canonical host path" >&2
    exit 1
  fi
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      disabled '' 'python:latest' >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a mutable API image" >&2
    exit 1
  fi
  "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
    temporary_static_pin \
    "$auth_secret_contract_file" \
    "$auth_secret_runtime_image"

  # Exercise the exact rollback transition: populate the runtime volume in
  # temporary mode, rerun the initializer in disabled mode, and prove the
  # API-readable verifier is removed rather than left dormant in the volume.
  docker run --rm --pull never \
    --network none --read-only --user 0:0 \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --env AUTH_LOGIN_MODE=temporary_static_pin \
    --env TEMPORARY_WEB_PIN_VERIFIER_SOURCE=/run/host-auth-secret/temporary_web_pin_verifier \
    --volume "$auth_secret_init_volume:/runtime-auth-secret" \
    --volume "$ROOT_DIR/scripts/init_auth_secret.sh:/usr/local/bin/init-auth-secret:ro" \
    --mount type=bind,source="$auth_secret_contract_file",target=/run/host-auth-secret/temporary_web_pin_verifier,readonly \
    --entrypoint /usr/local/bin/init-auth-secret \
    "$auth_secret_runtime_image"
  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_READ_SEARCH \
    --security-opt no-new-privileges:true \
    --volume "$auth_secret_init_volume:/runtime-auth-secret:ro" \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      verifier=/runtime-auth-secret/temporary_web_pin_verifier
      test -f "$verifier"
      test "$(stat -c %u:%g "$verifier")" = 100:101
      test "$(stat -c %a "$verifier")" = 400
    '
  docker run --rm --pull never \
    --network none --read-only --user 0:0 \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --env AUTH_LOGIN_MODE=disabled \
    --env TEMPORARY_WEB_PIN_VERIFIER_SOURCE=/run/host-auth-secret/temporary_web_pin_verifier \
    --volume "$auth_secret_init_volume:/runtime-auth-secret" \
    --volume "$ROOT_DIR/scripts/init_auth_secret.sh:/usr/local/bin/init-auth-secret:ro" \
    --mount type=bind,source=/dev/null,target=/run/host-auth-secret/temporary_web_pin_verifier,readonly \
    --entrypoint /usr/local/bin/init-auth-secret \
    "$auth_secret_runtime_image"
  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_READ_SEARCH \
    --security-opt no-new-privileges:true \
    --volume "$auth_secret_init_volume:/runtime-auth-secret:ro" \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c \
    'test -z "$(find /runtime-auth-secret -mindepth 1 -maxdepth 1 -print -quit)"'

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c \
    'printf unexpected > /fixture/unexpected-entry'
  # Immutable rotations deliberately retain sibling verifier files. Prove the
  # validator selects and exposes only the exact file instead of mounting its
  # containing directory into the isolated validation runtime.
  "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
    temporary_static_pin \
    "$auth_secret_contract_file" \
    "$auth_secret_runtime_image"
  docker run --rm --pull never \
    --network none --read-only --user 0:0 \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_file",target=/run/temporary-auth-secret/temporary_web_pin_verifier,readonly \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      test -f /run/temporary-auth-secret/temporary_web_pin_verifier
      test ! -e /run/temporary-auth-secret/unexpected-entry
    '
  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint rm "$auth_secret_runtime_image" /fixture/unexpected-entry

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      mv "/fixture/$1" /fixture/verifier-target
      ln -s verifier-target "/fixture/$1"
    ' fixture "$auth_secret_contract_token"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a symlinked verifier" >&2
    exit 1
  fi
  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      rm "/fixture/$1"
      mv /fixture/verifier-target "/fixture/$1"
    ' fixture "$auth_secret_contract_token"

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint ln "$auth_secret_runtime_image" \
    "/fixture/$auth_secret_contract_token" /fixture/verifier-hardlink
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a multiply-linked verifier" >&2
    exit 1
  fi
  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint rm "$auth_secret_runtime_image" /fixture/verifier-hardlink

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint chmod "$auth_secret_runtime_image" 0644 \
    "/fixture/$auth_secret_contract_token"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a world-readable verifier" >&2
    exit 1
  fi

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      chown 1:1 "/fixture/$1"
      chmod 0600 "/fixture/$1"
    ' fixture "$auth_secret_contract_token"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a non-root-owned verifier" >&2
    exit 1
  fi

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      printf "%s\n" invalid > "/fixture/$1"
      chown 0:0 "/fixture/$1"
      chmod 0600 "/fixture/$1"
    ' fixture "$auth_secret_contract_token"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted malformed verifier content" >&2
    exit 1
  fi

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      { printf "%064d\n" 0; printf trailing; } > "/fixture/$1"
      chown 0:0 "/fixture/$1"
      chmod 0600 "/fixture/$1"
    ' fixture "$auth_secret_contract_token"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted trailing verifier data" >&2
    exit 1
  fi

  docker run --rm --network none --read-only \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --mount type=bind,source="$auth_secret_contract_dir",target=/fixture \
    --entrypoint sh "$auth_secret_runtime_image" -eu -c '
      { printf "\\n"; printf "%064d" 0; } > "/fixture/$1"
      chown 0:0 "/fixture/$1"
      chmod 0600 "/fixture/$1"
    ' fixture "$auth_secret_contract_token"
  if "${auth_secret_validator_prefix[@]}" ./scripts/validate_temporary_auth_secret.sh \
      temporary_static_pin \
      "$auth_secret_contract_file" \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Temporary web PIN preflight accepted a leading newline without a final newline" >&2
    exit 1
  fi

  docker volume create "$auth_secret_malformed_volume" >/dev/null
  if docker run --rm --pull never \
      --network none \
      --read-only \
      --user 0:0 \
      --cap-drop ALL \
      --cap-add CHOWN \
      --cap-add DAC_OVERRIDE \
      --cap-add FOWNER \
      --security-opt no-new-privileges:true \
      --env AUTH_LOGIN_MODE=temporary_static_pin \
      --env TEMPORARY_WEB_PIN_VERIFIER_SOURCE=/run/host-auth-secret/temporary_web_pin_verifier \
      --volume "$auth_secret_malformed_volume:/runtime-auth-secret" \
      --volume "$ROOT_DIR/scripts/init_auth_secret.sh:/usr/local/bin/init-auth-secret:ro" \
      --mount type=bind,source="$auth_secret_contract_file",target=/run/host-auth-secret/temporary_web_pin_verifier,readonly \
      --entrypoint /usr/local/bin/init-auth-secret \
      "$auth_secret_runtime_image" >/dev/null 2>&1; then
    echo "Auth-secret initializer accepted a leading newline without a final newline" >&2
    exit 1
  fi
  docker volume rm "$auth_secret_malformed_volume" >/dev/null
else
  if [[ "${CI:-}" == "true" ]]; then
    echo "Root-owned temporary auth-secret runtime contract is required in CI" >&2
    exit 1
  fi
  echo "Root-owned temporary auth-secret runtime contract skipped: host bind ownership is unavailable."
fi
grep -Fq 'AUTH_LOGIN_MODE TEMPORARY_WEB_PIN_VERIFIER_FILE' scripts/deploy.sh
grep -Fq 'TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST TEMPORARY_WEB_PIN_EXPIRES_AT' scripts/deploy.sh
grep -Fq 'docker image inspect "$api_image"' scripts/validate_temporary_auth_secret.sh
grep -Fq 'if ((EUID != 0)); then' scripts/validate_temporary_auth_secret.sh
grep -Fq 'canonical_verifier_path="$(readlink -f -- "$verifier_path"' \
  scripts/validate_temporary_auth_secret.sh
grep -Fq 'verifier_links="$(stat -c '\''%h'\''' scripts/validate_temporary_auth_secret.sh
for isolated_flag in \
  '--pull never' '--network none' '--read-only' '--user 0:0' \
  '--cap-drop ALL' '--security-opt no-new-privileges:true'; do
  grep -Fq -- "$isolated_flag" scripts/validate_temporary_auth_secret.sh
done
grep -Fq 'test "$(stat -c %u:%g "$verifier")" = 0:0' \
  scripts/validate_temporary_auth_secret.sh
grep -Fq 'test "$(stat -c %a "$verifier")" = 600' \
  scripts/validate_temporary_auth_secret.sh
grep -Fq -- '--mount "type=bind,source=$verifier_path,target=/run/temporary-auth-secret/temporary_web_pin_verifier,readonly"' \
  scripts/validate_temporary_auth_secret.sh
grep -Fq 're.fullmatch(rb"[0-9a-f]{64}\x0a", verifier_bytes)' \
  scripts/validate_temporary_auth_secret.sh
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
awk '
  /^TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=/ {
    print "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=/var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/0123456789abcdef0123456789abcdef"; next
  }
  { print }
' "$contract_env" >"$versioned_auth_env"
chmod 0600 "$versioned_auth_env"
./scripts/validate_env.sh "$versioned_auth_env" production
versioned_rendered="$(docker compose --env-file "$versioned_auth_env" \
  -f compose.yaml -f compose.prod.yaml config --format json)"
jq --exit-status '
  [.services["auth-secret-init"].volumes[] |
    select(
      .type == "bind" and
      .source == "/var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/0123456789abcdef0123456789abcdef" and
      .target == "/run/host-auth-secret/temporary_web_pin_verifier" and
      .read_only == true and
      (.bind.create_host_path // false) == false
    )] | length == 1
' <<<"$versioned_rendered" >/dev/null
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
  .services.api.environment.FIELD_ENCRYPTION_KEY_ID == "primary" and
  .services.api.environment.FIELD_ENCRYPTION_WRITE_VERSION == "v1" and
  .services.api.environment.FIELD_ENCRYPTION_OLD_KEYS == "{}" and
  .services.api.environment.RESEARCH_PSEUDONYM_KEY == "contract-research-pseudonym-key-000000000" and
  .services.api.environment.ONBOARDING_INTEGRITY_KEY == "contract-onboarding-integrity-key-0000000" and
  .services.api.environment.AUDIT_LOG_RETENTION_DAYS == "365" and
  .services.api.environment.SYSTEM_ERROR_RETENTION_DAYS == "90" and
  .services.api.environment.OPERATIONAL_RETENTION_BATCH_SIZE == "500" and
  .services.worker.environment.FIELD_ENCRYPTION_KEY_ID == "primary" and
  .services.worker.environment.FIELD_ENCRYPTION_WRITE_VERSION == "v1" and
  .services.worker.environment.FIELD_ENCRYPTION_OLD_KEYS == "{}" and
  .services.worker.environment.RESEARCH_PSEUDONYM_KEY == "contract-research-pseudonym-key-000000000" and
  .services.worker.environment.ONBOARDING_INTEGRITY_KEY == "contract-onboarding-integrity-key-0000000" and
  .services.scheduler.environment.FIELD_ENCRYPTION_KEY_ID == "primary" and
  .services.scheduler.environment.FIELD_ENCRYPTION_WRITE_VERSION == "v1" and
  .services.scheduler.environment.FIELD_ENCRYPTION_OLD_KEYS == "{}" and
  .services.scheduler.environment.RESEARCH_PSEUDONYM_KEY == "contract-research-pseudonym-key-000000000" and
  .services.scheduler.environment.ONBOARDING_INTEGRITY_KEY == "contract-onboarding-integrity-key-0000000" and
  .services.migrate.environment.FIELD_ENCRYPTION_KEY_ID == "primary" and
  .services.migrate.environment.FIELD_ENCRYPTION_WRITE_VERSION == "v1" and
  .services.migrate.environment.FIELD_ENCRYPTION_OLD_KEYS == "{}" and
  .services.migrate.environment.RESEARCH_PSEUDONYM_KEY == "contract-research-pseudonym-key-000000000" and
  .services.migrate.environment.ONBOARDING_INTEGRITY_KEY == "contract-onboarding-integrity-key-0000000" and
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
  (.services.hermes.healthcheck.test[1] | contains("/run/service/main-hermes")) and
  (.services.hermes.healthcheck.test[1] | contains("/etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/type")) and
  (.services.hermes.healthcheck.test[1] | contains("/run/service/bumpabestie-hermes-control")) and
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
  .services["auth-secret-init"].image == .services.api.image and
  .services["auth-secret-init"].network_mode == "none" and
  .services["auth-secret-init"].read_only == true and
  .services["auth-secret-init"].cap_drop == ["ALL"] and
  .services["auth-secret-init"].cap_add == ["CHOWN", "DAC_OVERRIDE", "FOWNER"] and
  .services["auth-secret-init"].secrets == null and
  .services["auth-secret-init"].environment.AUTH_LOGIN_MODE == "temporary_static_pin" and
  .services["auth-secret-init"].environment.TEMPORARY_WEB_PIN_VERIFIER_SOURCE == "/run/host-auth-secret/temporary_web_pin_verifier" and
  ([.services["auth-secret-init"].volumes[] |
    select(
      .type == "bind" and
      .source == "/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier" and
      .target == "/run/host-auth-secret/temporary_web_pin_verifier" and
      .read_only == true and
      (.bind.create_host_path // false) == false
    )] | length == 1) and
  .services.api.depends_on["auth-secret-init"].condition == "service_completed_successfully" and
  .services.api.depends_on["hermes-staging-init"].condition == "service_completed_successfully" and
  .services.api.depends_on["app-secrets-init"].condition == "service_completed_successfully" and
  .services.worker.depends_on["app-secrets-init"].condition == "service_completed_successfully" and
  .services.scheduler.depends_on["app-secrets-init"].condition == "service_completed_successfully" and
  .services.api.environment.META_APP_SECRET_FILE == "/run/runtime-secrets/meta_app_secret" and
  .services.api.environment.AUTH_LOGIN_MODE == "temporary_static_pin" and
  .services.api.environment.TEMPORARY_WEB_PIN_VERIFIER == "" and
  .services.api.environment.TEMPORARY_WEB_PIN_VERIFIER_FILE == "/run/auth-secret/temporary_web_pin_verifier" and
  .services.api.environment.TEMPORARY_WEB_PIN_EXPIRES_AT == "2099-01-01T00:00:00Z" and
  .services.worker.environment.AUTH_LOGIN_MODE == "disabled" and
  .services.scheduler.environment.AUTH_LOGIN_MODE == "disabled" and
  .services.worker.environment.META_SYSTEM_USER_ACCESS_TOKEN_FILE == "/run/runtime-secrets/meta_system_user_access_token" and
  .services.worker.environment.OPS_ALERT_HMAC_SECRET_FILE == "/run/runtime-secrets/ops_alert_hmac_secret" and
  .services.scheduler.environment.OPS_ALERT_HMAC_SECRET_FILE == "/run/runtime-secrets/ops_alert_hmac_secret" and
  .services.api.environment.GOOGLE_OAUTH_CLIENT_SECRET_FILE == "/run/runtime-secrets/google_oauth_client_secret" and
  .services.worker.environment.META_ADS_OAUTH_CLIENT_SECRET_FILE == "/run/runtime-secrets/meta_ads_oauth_client_secret" and
  .services.scheduler.environment.GOOGLE_OAUTH_CLIENT_SECRET_FILE == "/run/runtime-secrets/google_oauth_client_secret" and
  (.services.api.secrets == null) and (.services.worker.secrets == null) and
  ([.services.api.volumes[] | select(.target == "/run/runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services.api.volumes[] | select(.target == "/run/auth-secret" and .read_only == true)] | length == 1) and
  ([.services.worker.volumes[] | select(.target == "/run/runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services.scheduler.volumes[] | select(.target == "/run/runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.target == "/runtime-secrets" and .read_only != true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .target == "/usr/local/bin/init-runtime-secrets" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .source == "/dev/null" and .target == "/run/optional-secrets/ops_alert_hmac_secret" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .source == "/dev/null" and .target == "/run/optional-secrets/google_oauth_client_secret" and .read_only == true)] | length == 1) and
  ([.services["app-secrets-init"].volumes[] | select(.type == "bind" and .source == "/dev/null" and .target == "/run/optional-secrets/meta_ads_oauth_client_secret" and .read_only == true)] | length == 1) and
  ([.services.api.volumes[] | select(.target == "/run/runtime-secrets") | .source] == [.services["app-secrets-init"].volumes[] | select(.target == "/runtime-secrets") | .source]) and
  ([.services.api.volumes[] | select(.target == "/run/auth-secret") | .source] == [.services["auth-secret-init"].volumes[] | select(.target == "/runtime-auth-secret") | .source]) and
  (.secrets | has("temporary_web_pin_verifier") | not) and
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
hermes_healthcheck="$(jq --raw-output '.services.hermes.healthcheck.test[1]' <<<"$rendered")"
sh -n -c "$hermes_healthcheck"
mkdir -p \
  "$hermes_health_probe/command-main-only" \
  "$hermes_health_probe/command-all-up" \
  "$hermes_health_probe/control-absent" \
  "$hermes_health_probe/control-present/s6-rc.d/bumpabestie-hermes-control" \
  "$hermes_health_probe/curl-fail" \
  "$hermes_health_probe/curl-success"
cat > "$hermes_health_probe/command-main-only/s6-svstat" <<'EOF'
#!/bin/sh
if [ "$1" = /run/service/main-hermes ]; then
  printf 'up (pid 100) 1 seconds\n'
  exit 0
fi
exit 1
EOF
cat > "$hermes_health_probe/command-all-up/s6-svstat" <<'EOF'
#!/bin/sh
printf 'up (pid 100) 1 seconds\n'
EOF
cat > "$hermes_health_probe/curl-fail/curl" <<'EOF'
#!/bin/sh
exit 1
EOF
cat > "$hermes_health_probe/curl-success/curl" <<'EOF'
#!/bin/sh
exit 0
EOF
printf 'longrun\n' > \
  "$hermes_health_probe/control-present/s6-rc.d/bumpabestie-hermes-control/type"
chmod 0555 \
  "$hermes_health_probe/command-main-only/s6-svstat" \
  "$hermes_health_probe/command-all-up/s6-svstat" \
  "$hermes_health_probe/curl-fail/curl" \
  "$hermes_health_probe/curl-success/curl"
health_probe_image='python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df'
docker run --rm --network none --read-only \
  --env HERMES_CONTROL_PORT=8699 --env PATH=/stubs:/usr/local/bin:/usr/bin:/bin \
  --volume "$hermes_health_probe/command-main-only:/command:ro" \
  --volume "$hermes_health_probe/control-absent:/etc/s6-overlay:ro" \
  --volume "$hermes_health_probe/curl-fail:/stubs:ro" \
  "$health_probe_image" sh -eu -c "$hermes_healthcheck"
if docker run --rm --network none --read-only \
    --env HERMES_CONTROL_PORT=8699 --env PATH=/stubs:/usr/local/bin:/usr/bin:/bin \
    --volume "$hermes_health_probe/command-main-only:/command:ro" \
    --volume "$hermes_health_probe/control-present:/etc/s6-overlay:ro" \
    --volume "$hermes_health_probe/curl-success:/stubs:ro" \
    "$health_probe_image" sh -eu -c "$hermes_healthcheck"; then
  echo "Hermes healthcheck accepted a failed control service in a control-capable image" >&2
  exit 1
fi
docker run --rm --network none --read-only \
  --env HERMES_CONTROL_PORT=8699 --env PATH=/stubs:/usr/local/bin:/usr/bin:/bin \
  --volume "$hermes_health_probe/command-all-up:/command:ro" \
  --volume "$hermes_health_probe/control-present:/etc/s6-overlay:ro" \
  --volume "$hermes_health_probe/curl-success:/stubs:ro" \
  "$health_probe_image" sh -eu -c "$hermes_healthcheck"

stop_line="$(grep -n -F "\"\${compose[@]}\" stop --timeout 60 caddy web api worker scheduler" scripts/deploy.sh | cut -d: -f1)"
image_pull_line="$(grep -n -F '"${compose[@]}" --profile tools pull caddy postgres redis web api backup hermes' scripts/deploy.sh | cut -d: -f1)"
target_auth_secret_preflight_line="$(grep -n -F 'sudo -n /usr/local/sbin/bumpabestie-validate-temporary-auth-secret' scripts/deploy.sh | cut -d: -f1)"
auth_secret_preflight_count="$(grep -Fc 'sudo -n /usr/local/sbin/bumpabestie-validate-temporary-auth-secret' scripts/deploy.sh)"
target_auth_secret_match_line="$(grep -n -F '|| ! cmp -s' scripts/deploy.sh | cut -d: -f1)"
rollback_enable_line="$(grep -n -F 'automatic_rollback_available=1' scripts/deploy.sh | cut -d: -f1)"
backup_line="$(grep -n -E '^[[:space:]]+backup$' scripts/deploy.sh | cut -d: -f1)"
backup_init_line="$(grep -n -F 'run --rm --no-deps backup-data-init' scripts/deploy.sh | cut -d: -f1)"
writer_stop_attempted_line="$(grep -n -F 'writer_stop_attempted=1' scripts/deploy.sh | cut -d: -f1)"
writer_guard_lines="$(grep -n -E '^assert_application_writers_stopped$' scripts/deploy.sh | cut -d: -f1)"
first_writer_guard_line="$(sed -n '1p' <<<"$writer_guard_lines")"
boundary_writer_guard_line="$(sed -n '2p' <<<"$writer_guard_lines")"
forward_boundary_line="$(grep -n -F 'write_promotion_state "$promotion_state_file" FORWARD_BOUNDARY' scripts/deploy.sh | cut -d: -f1)"
role_init_line="$(grep -n -F '/docker-entrypoint-initdb.d/10-app-role.sh' scripts/deploy.sh | cut -d: -f1)"
migrate_line="$(grep -n -F "\"\${compose[@]}\" --profile tools run --rm migrate" scripts/deploy.sh | cut -d: -f1)"
reconcile_line="$(grep -n -F 'ENV_FILE=.env.production ./scripts/reconcile_hermes_profiles.sh' scripts/deploy.sh | cut -d: -f1)"
# The following patterns intentionally match literal shell source.
# shellcheck disable=SC2016
application_start_line="$(grep -n -F -- '--profile async up -d --wait --wait-timeout 240 --remove-orphans' scripts/deploy.sh | cut -d: -f1)"
require_single_line_number stop_line "$stop_line"
require_single_line_number image_pull_line "$image_pull_line"
require_single_line_number target_auth_secret_preflight_line "$target_auth_secret_preflight_line"
require_single_line_number target_auth_secret_match_line "$target_auth_secret_match_line"
require_single_line_number rollback_enable_line "$rollback_enable_line"
require_single_line_number backup_line "$backup_line"
require_single_line_number backup_init_line "$backup_init_line"
require_single_line_number writer_stop_attempted_line "$writer_stop_attempted_line"
require_single_line_number first_writer_guard_line "$first_writer_guard_line"
require_single_line_number boundary_writer_guard_line "$boundary_writer_guard_line"
require_single_line_number forward_boundary_line "$forward_boundary_line"
require_single_line_number role_init_line "$role_init_line"
require_single_line_number migrate_line "$migrate_line"
require_single_line_number reconcile_line "$reconcile_line"
require_single_line_number application_start_line "$application_start_line"
if [[ -z "$stop_line" || -z "$image_pull_line" \
  || -z "$target_auth_secret_preflight_line" \
  || -z "$target_auth_secret_match_line" \
  || "$auth_secret_preflight_count" != 1 \
  || -z "$rollback_enable_line" \
  || -z "$backup_init_line" || -z "$backup_line" \
  || "$(wc -l <<<"$writer_guard_lines" | tr -d ' ')" != 2 \
  || "$image_pull_line" -ge "$target_auth_secret_match_line" \
  || "$target_auth_secret_match_line" -ge "$target_auth_secret_preflight_line" \
  || "$target_auth_secret_preflight_line" -ge "$stop_line" \
  || "$writer_stop_attempted_line" -ge "$stop_line" \
  || "$stop_line" -ge "$first_writer_guard_line" \
  || "$first_writer_guard_line" -ge "$backup_init_line" \
  || "$backup_init_line" -ge "$backup_line" \
  || "$backup_line" -ge "$boundary_writer_guard_line" \
  || "$boundary_writer_guard_line" -ge "$forward_boundary_line" \
  || "$forward_boundary_line" -ge "$migrate_line" ]]; then
  echo "Deployment does not quiesce application writers before the recovery-point backup" >&2
  exit 1
fi
grep -Fq 'application_writer_services=(api worker scheduler)' scripts/deploy.sh
grep -Fq 'ps --all -q "$service"' scripts/deploy.sh
grep -Fq "state=\"\$(docker inspect --format '{{.State.Status}}' \"\$container_id\")\"" scripts/deploy.sh
grep -Fq 'if [[ "$state" != "exited" ]]; then' scripts/deploy.sh
grep -Fq 'if ((writer_stop_attempted && ${#previous_writer_containers[@]} > 0)); then' scripts/deploy.sh
deploy_lock_line="$(grep -n -F 'acquire_maintenance_lock' scripts/deploy.sh | cut -d: -f1)"
deploy_env_line="$(grep -n -F 'if [[ ! -f .env.production ]]' scripts/deploy.sh | cut -d: -f1)"
release_helper_line="$(grep -n -F 'source "$ROOT_DIR/scripts/release_boundary.sh"' scripts/deploy.sh | cut -d: -f1)"
release_load_line="$(grep -n -F 'load_release_boundary .deployed-release.json' scripts/deploy.sh | cut -d: -f1)"
target_validate_line="$(grep -n -F './scripts/validate_env.sh .env.production production' scripts/deploy.sh | cut -d: -f1)"
scheduled_lock_line="$(grep -n -F 'acquire_maintenance_lock' scripts/scheduled_backup.sh | cut -d: -f1)"
scheduled_env_line="$(grep -n -F "env_file=\"\${ENV_FILE:-.env.production}\"" scripts/scheduled_backup.sh | cut -d: -f1)"
require_single_line_number deploy_lock_line "$deploy_lock_line"
require_single_line_number deploy_env_line "$deploy_env_line"
require_single_line_number release_helper_line "$release_helper_line"
require_single_line_number release_load_line "$release_load_line"
require_single_line_number target_validate_line "$target_validate_line"
require_single_line_number scheduled_lock_line "$scheduled_lock_line"
require_single_line_number scheduled_env_line "$scheduled_env_line"
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
grep -Fq 'restore_previous_release_boundary' scripts/deploy.sh
grep -Fq 'auth: {' scripts/deploy.sh
grep -Fq 'previous_auth: {' infra/bin/bumpabestie-promote
grep -Fq 'canonical_previous_env_sha256' infra/bin/bumpabestie-promote
if grep -Fq './scripts/validate_temporary_auth_secret.sh' scripts/deploy.sh; then
  echo "Deployment elevates the mutable checkout validator" >&2
  exit 1
fi
grep -Fq 'for service in api worker scheduler web hermes caddy postgres redis' scripts/deploy.sh
grep -Fq 'actual_image="$(running_image "$service")"' scripts/deploy.sh
grep -Fq 'automatic_rollback_available=1' scripts/deploy.sh
grep -Fq 'elif ((deployment_started && automatic_rollback_available))' scripts/deploy.sh
grep -Fq 'SMOKE_ORIGIN_ADDRESS=127.0.0.1' scripts/deploy.sh
grep -Fq 'SMOKE_OVERALL_TIMEOUT_SECONDS=180' scripts/deploy.sh
grep -Fq 'if ! SMOKE_SCHEME=https' scripts/deploy.sh
grep -Fq 'SMOKE_ORIGIN_ADDRESS=' scripts/deploy.sh
grep -Fq 'SMOKE_OVERALL_TIMEOUT_SECONDS=60' scripts/deploy.sh
test "$(grep -Ec '^[[:space:]]*run_production_smoke$' scripts/deploy.sh)" = 1
test "$(grep -Fc '&& run_production_smoke; then' scripts/deploy.sh)" = 1
test "$(grep -Fc '&& run_production_smoke' scripts/deploy.sh)" = 2
grep -Fq 'if docker start "${previous_writer_containers[@]}" >/dev/null' scripts/deploy.sh
grep -Fq '&& "${compose[@]}" --profile async up -d --wait --wait-timeout 240' scripts/deploy.sh
grep -Fq 'COPY --chmod=0444 infra/hermes/control-type /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/type' infra/hermes/Dockerfile
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
promotion_lock_line="$(grep -n -F 'acquire_maintenance_lock' scripts/promote_release.sh | cut -d: -f1)"
promotion_record_line="$(grep -n -F 'load_release_boundary .deployed-release.json' scripts/promote_release.sh | cut -d: -f1)"
promotion_checkout_line="$(grep -n -F 'git checkout --detach "$revision"' scripts/promote_release.sh | cut -d: -f1)"
promotion_pointer_line="$(grep -n -F 'rewrite_release_pointers .env.production' scripts/promote_release.sh | cut -d: -f1)"
promotion_exec_line="$(grep -n -F '"$ROOT_DIR/scripts/deploy.sh"' scripts/promote_release.sh | cut -d: -f1)"
require_single_line_number promotion_lock_line "$promotion_lock_line"
require_single_line_number promotion_record_line "$promotion_record_line"
require_single_line_number promotion_checkout_line "$promotion_checkout_line"
require_single_line_number promotion_pointer_line "$promotion_pointer_line"
require_single_line_number promotion_exec_line "$promotion_exec_line"
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
grep -Fq '/usr/local/sbin/bumpabestie-validate-temporary-auth-secret' scripts/bootstrap_server.sh
grep -Fq '/etc/sudoers.d/bumpabestie-temporary-auth-secret' scripts/bootstrap_server.sh
grep -Fq 'visudo -cf' scripts/bootstrap_server.sh
grep -Fq 'apt-get install -y ca-certificates curl git gnupg jq python3 sudo' \
  scripts/bootstrap_server.sh
if grep -Fqi 'fail2ban' scripts/bootstrap_server.sh; then
  echo "Bootstrap reintroduces the removed Fail2ban package or service" >&2
  exit 1
fi
grep -Fq 'if [[ -n "${ADMIN_SSH_CIDR:-}" ]]; then' scripts/bootstrap_server.sh
grep -Fq "ufw allow 22/tcp comment 'BumpaBestie key-only SSH'" scripts/bootstrap_server.sh
if grep -Fq 'Set ADMIN_SSH_CIDR' scripts/bootstrap_server.sh; then
  echo "Bootstrap still requires a transient administrator CIDR" >&2
  exit 1
fi
test -f infra/sudoers/bumpabestie-temporary-auth-secret
grep -Fxq 'Defaults!/usr/local/sbin/bumpabestie-validate-temporary-auth-secret env_reset,!setenv,secure_path=/usr/sbin\:/usr/bin\:/sbin\:/bin' \
  infra/sudoers/bumpabestie-temporary-auth-secret
grep -Fxq 'bumpabestie ALL=(root) NOPASSWD: /usr/local/sbin/bumpabestie-validate-temporary-auth-secret' \
  infra/sudoers/bumpabestie-temporary-auth-secret
if command -v visudo >/dev/null 2>&1; then
  visudo -cf infra/sudoers/bumpabestie-temporary-auth-secret >/dev/null
fi
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
rollback_boundary_line="$(grep -n -F 'complete hybrid rollback boundary before recreating' scripts/deploy.sh | cut -d: -f1)"
rollback_auth_init_line="$(grep -n -F -- '--abort-on-container-exit --exit-code-from auth-secret-init auth-secret-init' scripts/deploy.sh | cut -d: -f1)"
rollback_app_recreate_line="$(grep -n -F -- '--no-deps "${rollback_services[@]}"' scripts/deploy.sh | cut -d: -f1)"
require_single_line_number rollback_boundary_line "$rollback_boundary_line"
require_single_line_number rollback_auth_init_line "$rollback_auth_init_line"
require_single_line_number rollback_app_recreate_line "$rollback_app_recreate_line"
if [[ -z "$rollback_boundary_line" || -z "$rollback_auth_init_line" \
  || -z "$rollback_app_recreate_line" \
  || "$rollback_boundary_line" -ge "$rollback_auth_init_line" \
  || "$rollback_auth_init_line" -ge "$rollback_app_recreate_line" ]]; then
  echo "Rollback does not restore auth config and rerun its initializer before application recreation" >&2
  exit 1
fi
grep -Fq '&& API_IMAGE="$target_api_image"' scripts/deploy.sh
hybrid_pointer_line="$(grep -n -F 'if ((rollback_result == 0)) && rewrite_release_boundary .env.production' scripts/deploy.sh | cut -d: -f1)"
hybrid_metadata_line="$(grep -n -F '&& persist_release_metadata' scripts/deploy.sh | cut -d: -f1)"
require_single_line_number hybrid_pointer_line "$hybrid_pointer_line"
require_single_line_number hybrid_metadata_line "$hybrid_metadata_line"
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
grep -Fq '(non_document_csp) {' infra/caddy/Caddyfile
grep -Fq 'header ?Content-Security-Policy "default-src '\''none'\'';' \
  infra/caddy/Caddyfile
grep -Fq 'header_up -Content-Security-Policy' infra/caddy/Caddyfile
grep -Fq 'header_up -Content-Security-Policy-Report-Only' infra/caddy/Caddyfile
grep -Fq 'header_up -X-Nonce' infra/caddy/Caddyfile
grep -Fq 'trusted_proxies_strict' infra/caddy/Caddyfile
grep -Fq 'client_ip_headers CF-Connecting-IP' infra/caddy/Caddyfile
grep -Fq 'header_up X-Forwarded-For {client_ip}' infra/caddy/Caddyfile
grep -Fq 'header_up X-Real-IP {client_ip}' infra/caddy/Caddyfile
grep -Fq 'header_up X-Bumpa-Client-IP {client_ip}' infra/caddy/Caddyfile
grep -Fq 'header_up -CF-Connecting-IP' infra/caddy/Caddyfile
if grep -Fq 'trusted_proxies static private_ranges' infra/caddy/Caddyfile; then
  echo "Caddy trusts private peers to supply public client-IP headers" >&2
  exit 1
fi
cloudflare_ranges="$(
  awk '/^[[:space:]]*trusted_proxies static / { for (field_number = 3; field_number <= NF; field_number++) print $field_number }' \
    infra/caddy/Caddyfile
)"
test "$(wc -w <<<"$cloudflare_ranges" | tr -d '[:space:]')" = 22
for cloudflare_range in \
  173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 \
  141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 \
  197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 \
  104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 \
  2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 \
  2a06:98c0::/29 2c0f:f248::/32; do
  grep -Fxq "$cloudflare_range" <<<"$cloudflare_ranges"
done
grep -Fq -- '--forwarded-allow-ips=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7' \
  apps/api/Dockerfile
if grep -Fq -- '--forwarded-allow-ips=*' apps/api/Dockerfile \
  || grep -Fq -- '"--forwarded-allow-ips", "*"' compose.yaml; then
  echo "Uvicorn trusts proxy headers from every network peer" >&2
  exit 1
fi
if grep -Eq "script-src[^;]*'unsafe-inline'" \
  infra/caddy/Caddyfile apps/web/lib/content-security-policy.ts; then
  echo "Document script CSP permits unsafe inline execution" >&2
  exit 1
fi
grep -Fq '"style-src-attr '\''unsafe-inline'\''"' \
  apps/web/lib/content-security-policy.ts
grep -Fq 'await connection();' apps/web/app/layout.tsx
grep -Fq 'requestHeaders.set(CONTENT_SECURITY_POLICY_HEADER, contentSecurityPolicy);' \
  apps/web/middleware.ts
grep -Fq 'requestHeaders.set(CSP_NONCE_REQUEST_HEADER, nonce);' \
  apps/web/middleware.ts
grep -Fq '://{$WWW_DOMAIN:www.bumpabestie.localhost} {' infra/caddy/Caddyfile
grep -Fq 'redir {$CADDY_SITE_SCHEME:http}://{$APP_DOMAIN:bumpabestie.localhost}{uri} 308' \
  infra/caddy/Caddyfile
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
scheduled_backup_line="$(grep -n -E '^[[:space:]]*"\$\{compose\[@\]\}" run --rm --no-deps backup$' scripts/scheduled_backup.sh | cut -d: -f1)"
require_single_line_number scheduled_backup_init_line "$scheduled_backup_init_line"
require_single_line_number scheduled_backup_line "$scheduled_backup_line"
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
grep -Fq 'install -m 0755 -o root -g root "$docker_firewall_source" "$docker_firewall_binary"' \
  scripts/bootstrap_server.sh
docker_firewall_state_gate_line="$(grep -n -F 'if [[ -n "$docker_firewall_source" && -e "$docker_firewall_state" ]]' scripts/bootstrap_server.sh | cut -d: -f1)"
docker_firewall_verify_line="$(grep -n -F '"$docker_firewall_binary" verify-state' scripts/bootstrap_server.sh | cut -d: -f1)"
docker_firewall_pregate_start_line="$(grep -n -F 'systemctl start "$docker_firewall_pregate_unit"' scripts/bootstrap_server.sh | cut -d: -f1)"
docker_firewall_enable_line="$(grep -n -F 'systemctl enable --now "$docker_firewall_unit"' scripts/bootstrap_server.sh | cut -d: -f1)"
require_single_line_number docker_firewall_state_gate_line "$docker_firewall_state_gate_line"
require_single_line_number docker_firewall_verify_line "$docker_firewall_verify_line"
require_single_line_number docker_firewall_pregate_start_line "$docker_firewall_pregate_start_line"
require_single_line_number docker_firewall_enable_line "$docker_firewall_enable_line"
if [[ -z "$docker_firewall_state_gate_line" || -z "$docker_firewall_verify_line" \
  || -z "$docker_firewall_pregate_start_line" \
  || -z "$docker_firewall_enable_line" \
  || "$docker_firewall_state_gate_line" -ge "$docker_firewall_verify_line" \
  || "$docker_firewall_verify_line" -ge "$docker_firewall_pregate_start_line" \
  || "$docker_firewall_pregate_start_line" -ge "$docker_firewall_enable_line" ]]; then
  echo "Bootstrap can enable the Docker firewall before validated persistent state" >&2
  exit 1
fi
grep -Fq 'install -d -m 0755 -o root -g root /etc/systemd/system/docker.service.d' \
  scripts/bootstrap_server.sh
grep -Fq '"/etc/systemd/system/docker.service.d/$docker_firewall_dropin"' \
  scripts/bootstrap_server.sh
docker_firewall_pregate_unit=infra/systemd/bumpabestie-cloudflare-origin-pregate.service
grep -Fxq 'Before=docker.service' "$docker_firewall_pregate_unit"
grep -Fxq 'After=network-online.target' "$docker_firewall_pregate_unit"
grep -Fxq 'OnFailure=bumpabestie-cloudflare-docker-firewall-failure.service' \
  "$docker_firewall_pregate_unit"
grep -Fxq 'ExecStart=/usr/local/sbin/bumpabestie-cloudflare-docker-firewall apply-pregate-state' \
  "$docker_firewall_pregate_unit"
grep -Fxq 'RuntimeDirectory=bumpabestie-cloudflare-docker-firewall' \
  "$docker_firewall_pregate_unit"
grep -Fxq 'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW' \
  "$docker_firewall_pregate_unit"
if grep -Eq '^RemainAfterExit=' "$docker_firewall_pregate_unit"; then
  echo "Pre-Docker gate must run again on every Docker activation" >&2
  exit 1
fi
docker_firewall_dropin=infra/systemd/docker.service.d/10-bumpabestie-cloudflare-origin-pregate.conf
grep -Fxq 'Requires=bumpabestie-cloudflare-origin-pregate.service' \
  "$docker_firewall_dropin"
grep -Fxq 'After=bumpabestie-cloudflare-origin-pregate.service' \
  "$docker_firewall_dropin"
docker_firewall_unit=infra/systemd/bumpabestie-cloudflare-docker-firewall.service
grep -Fxq 'After=network-online.target docker.service' "$docker_firewall_unit"
grep -Fxq 'BindsTo=docker.service' "$docker_firewall_unit"
grep -Fxq 'PartOf=docker.service' "$docker_firewall_unit"
grep -Fxq 'OnFailure=bumpabestie-cloudflare-docker-firewall-failure.service' \
  "$docker_firewall_unit"
grep -Fxq 'ExecStart=/usr/local/sbin/bumpabestie-cloudflare-docker-firewall apply-state' \
  "$docker_firewall_unit"
grep -Fxq 'RuntimeDirectory=bumpabestie-cloudflare-docker-firewall' \
  "$docker_firewall_unit"
grep -Fxq 'RuntimeDirectoryMode=0700' "$docker_firewall_unit"
grep -Fxq 'ProtectSystem=strict' "$docker_firewall_unit"
grep -Fxq 'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW' "$docker_firewall_unit"
grep -Fxq 'WantedBy=docker.service' "$docker_firewall_unit"
if grep -Eq '^ExecStop=' "$docker_firewall_unit"; then
  echo "Stopping the Docker firewall unit must not remove its managed rules" >&2
  exit 1
fi
docker_firewall_failure_unit=infra/systemd/bumpabestie-cloudflare-docker-firewall-failure.service
grep -Fxq 'Conflicts=docker.service' "$docker_firewall_failure_unit"
grep -Fxq 'Before=docker.service' "$docker_firewall_failure_unit"
grep -Fxq 'ExecStart=/usr/bin/true' "$docker_firewall_failure_unit"
if grep -Eq '^ExecStart=.*/systemctl ' "$docker_firewall_failure_unit"; then
  echo "Firewall failure handling must not launch a nested blocking systemctl" >&2
  exit 1
fi
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
  /^FIELD_ENCRYPTION_OLD_KEYS=/ {
    print "FIELD_ENCRYPTION_OLD_KEYS={\"primary\":\"old-field-key-material-000000000000\"}"; next
  }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted the current key ID in the old-key ring" >&2
  exit 1
fi

awk '
  /^FIELD_ENCRYPTION_OLD_KEYS=/ {
    print "FIELD_ENCRYPTION_OLD_KEYS={\"old-2025\":\"too-short\"}"; next
  }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted weak old field-encryption key material" >&2
  exit 1
fi

awk '
  /^FIELD_ENCRYPTION_WRITE_VERSION=/ {
    print "FIELD_ENCRYPTION_WRITE_VERSION=v2"; next
  }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "First dual-reader production validation accepted v2 field-encryption writes" >&2
  exit 1
fi

awk '
  /^FIELD_ENCRYPTION_WRITE_VERSION=/ {
    print "FIELD_ENCRYPTION_WRITE_VERSION=v3"; next
  }
  { print }
' "$contract_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted an unsupported field-encryption write version" >&2
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
  /^AGENT_BACKEND=/ { print "AGENT_BACKEND=hermes"; next }
  /^BUMPA_BACKEND=/ { print "BUMPA_BACKEND=bumpa"; next }
  { print }
' "$whatsapp_auth_env" > "$live_env"
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
  /^WHATSAPP_BACKEND=/ { print "WHATSAPP_BACKEND=meta"; next }
  /^META_PRIMARY_SENDER_ENABLED=/ { print "META_PRIMARY_SENDER_ENABLED=false"; next }
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
' "$contract_env" > "$temporary_verification_env"
chmod 0600 "$temporary_verification_env"
./scripts/validate_env.sh "$temporary_verification_env" production >/dev/null

temporary_verification_rendered="$(docker compose --env-file "$temporary_verification_env" \
  -f compose.yaml -f compose.prod.yaml --profile async --profile tools config --format json)"
if ! jq --exit-status '
  .services.api.environment.AUTH_LOGIN_MODE == "temporary_static_pin" and
  .services.api.environment.WHATSAPP_BACKEND == "meta" and
  .services.api.environment.META_PRIMARY_SENDER_ENABLED == "false" and
  .services.api.environment.META_TEST_SENDER_VERIFICATION_MODE == "inbound_replies_only" and
  .services.worker.environment.AUTH_LOGIN_MODE == "disabled" and
  .services.worker.environment.WHATSAPP_BACKEND == "meta" and
  .services.worker.environment.META_PRIMARY_SENDER_ENABLED == "false" and
  .services.worker.environment.META_TEST_SENDER_VERIFICATION_MODE == "inbound_replies_only" and
  .services.migrate.environment.WHATSAPP_BACKEND == "disabled" and
  .services.migrate.environment.META_TEST_SENDER_VERIFICATION_MODE == "disabled"
' <<<"$temporary_verification_rendered" >/dev/null; then
  echo "Production Compose did not preserve the temporary-PIN reply-only Meta test lane" >&2
  exit 1
fi

awk '
  /^META_PRIMARY_SENDER_ENABLED=/ { print "META_PRIMARY_SENDER_ENABLED=true"; next }
  { print }
' "$temporary_verification_env" > "$invalid_env"
chmod 0600 "$invalid_env"
if ./scripts/validate_env.sh "$invalid_env" production >/dev/null 2>&1; then
  echo "Production validation accepted temporary-PIN Meta mode with the primary sender enabled" >&2
  exit 1
fi

# The one-shot migration process intentionally disables provider backends. A
# live Meta test-sender setting must therefore remain available to the API while
# being neutralized for migrations, whose settings are loaded before Alembic
# can run. This reproduces the production release boundary rather than merely
# checking the all-disabled contract fixture above.
verification_rendered="$(docker compose --env-file "$verification_env" \
  -f compose.yaml -f compose.prod.yaml --profile tools config --format json)"
if ! jq --exit-status '
  .services.api.environment.WHATSAPP_BACKEND == "meta" and
  .services.api.environment.META_TEST_SENDER_VERIFICATION_MODE == "inbound_replies_only" and
  .services.migrate.environment.WHATSAPP_BACKEND == "disabled" and
  .services.migrate.environment.META_TEST_SENDER_VERIFICATION_MODE == "disabled"
' <<<"$verification_rendered" >/dev/null; then
  jq '{
    api: (.services.api.environment | {
      WHATSAPP_BACKEND,
      META_TEST_SENDER_VERIFICATION_MODE
    }),
    migrate: (.services.migrate.environment | {
      WHATSAPP_BACKEND,
      META_TEST_SENDER_VERIFICATION_MODE
    })
  }' <<<"$verification_rendered" >&2
  exit 1
fi

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

# The third-party Hermes base is digest-pinned, but its Debian security overlay
# is intentionally refreshed on every protected and publication build. A cached
# apt layer can otherwise retain a package after Debian has shipped a fix.
grep -Fq '      fail-fast: false' .github/workflows/ci.yml
grep -Fq "          no-cache: \${{ matrix.name == 'hermes' }}" .github/workflows/ci.yml
grep -Fq "          no-cache: \${{ matrix.name == 'hermes' }}" .github/workflows/publish-images.yml
grep -Fq "dpkg-query --show --showformat='\${Version}' libxfont2" infra/hermes/Dockerfile
grep -Fq "ge '1:2.0.6-1+deb13u1'" infra/hermes/Dockerfile
grep -Fq 'mcp==1.28.1' infra/hermes/Dockerfile
grep -Fq 'pillow==12.3.0' infra/hermes/Dockerfile
grep -Fq '/opt/hermes/node_modules/@eslint/config-array/node_modules/brace-expansion' infra/hermes/Dockerfile
grep -Fq 'npm ls --prefix /opt/hermes --omit=dev --depth=0' infra/hermes/Dockerfile
grep -Fq 'from PIL import Image' infra/hermes/Dockerfile
grep -Fq '/opt/hermes/.venv/bin/hermes --help' infra/hermes/Dockerfile

echo "Production environment and immutable Compose contracts passed"
