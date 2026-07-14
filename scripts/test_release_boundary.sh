#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/release_boundary.sh"

test_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$test_dir"
}
trap cleanup EXIT

old_revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
new_revision="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
old_infra="sha-cccccccccccccccccccccccccccccccccccccccc"
new_infra="sha-dddddddddddddddddddddddddddddddddddddddd"
old_api="registry.example/bumpa/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
old_web="registry.example/bumpa/web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
old_caddy="registry.example/bumpa/caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
old_postgres="registry.example/bumpa/postgres@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
old_redis="redis:7-alpine@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
old_backup="registry.example/bumpa/backup@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
old_hermes="registry.example/bumpa/hermes@sha256:abababababababababababababababababababababababababababababababab"
new_api="registry.example/bumpa/api@sha256:1111111111111111111111111111111111111111111111111111111111111111"
new_web="registry.example/bumpa/web@sha256:2222222222222222222222222222222222222222222222222222222222222222"
new_caddy="registry.example/bumpa/caddy@sha256:3333333333333333333333333333333333333333333333333333333333333333"
new_postgres="registry.example/bumpa/postgres@sha256:4444444444444444444444444444444444444444444444444444444444444444"
new_backup="registry.example/bumpa/backup@sha256:5555555555555555555555555555555555555555555555555555555555555555"
new_hermes="registry.example/bumpa/hermes@sha256:6666666666666666666666666666666666666666666666666666666666666666"
legacy_verifier_path="/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier"
versioned_verifier_path="/var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/0123456789abcdef0123456789abcdef"
for invalid_verifier_path in \
  /var/lib/../temporary_web_pin_verifier \
  /var/lib/other/temporary_web_pin_verifier \
  /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/short; do
  if validate_temporary_verifier_host_path "$invalid_verifier_path"; then
    echo "Unsafe temporary verifier path was accepted" >&2
    exit 1
  fi
done
validate_temporary_verifier_host_path "$legacy_verifier_path"
validate_temporary_verifier_host_path "$versioned_verifier_path"

release_file="$test_dir/.deployed-release.json"
jq --null-input \
  --arg revision "$old_revision" \
  --arg image_tag "sha-$old_revision" \
  --arg infra_image_tag "$old_infra" \
  --arg api "$old_api" --arg web "$old_web" --arg caddy "$old_caddy" \
  --arg postgres "$old_postgres" --arg redis "$old_redis" \
  --arg backup "$old_backup" --arg hermes "$old_hermes" \
  '{
    revision: $revision,
    image_tag: $image_tag,
    infra_image_tag: $infra_image_tag,
    images: {
      api: $api, worker: $api, scheduler: $api, web: $web,
      caddy: $caddy, postgres: $postgres, redis: $redis,
      backup: $backup, hermes: $hermes
    },
    auth: {
      login_mode: "disabled",
      temporary_web_pin_verifier_file: "",
      temporary_web_pin_verifier_file_host: "",
      temporary_web_pin_expires_at: "",
      whatsapp_backend: "disabled"
    }
  }' > "$release_file"
chmod 0600 "$release_file"
load_release_boundary "$release_file"
test "$RELEASE_REVISION" = "$old_revision"
test "$RELEASE_OPERATIONS_REVISION" = "$old_revision"
test "$RELEASE_API_IMAGE" = "$old_api"
test "$RELEASE_BACKUP_IMAGE" = "$old_backup"
test "$RELEASE_AUTH_LOGIN_MODE" = disabled
test -z "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"
jq --arg operations_revision "$new_revision" \
  '.operations_revision = $operations_revision' "$release_file" > "$test_dir/release-with-operations.json"
mv "$test_dir/release-with-operations.json" "$release_file"
chmod 0600 "$release_file"
load_release_boundary "$release_file"
test "$RELEASE_OPERATIONS_REVISION" = "$new_revision"

env_file="$test_dir/.env.production"
printf '%s\n' \
  '# release-boundary contract' \
  "DEPLOY_REF=$old_revision" \
  "IMAGE_TAG=sha-$old_revision" \
  "INFRA_IMAGE_TAG=$old_infra" \
  "API_IMAGE=$old_api" \
  'JWT_SECRET=preserve=this=value exactly' \
  "WEB_IMAGE=$old_web" \
  "CADDY_IMAGE=$old_caddy" \
  "POSTGRES_IMAGE=$old_postgres" \
  "BACKUP_IMAGE=$old_backup" \
  "HERMES_IMAGE=$old_hermes" \
  'AUTH_LOGIN_MODE=disabled' \
  'TEMPORARY_WEB_PIN_VERIFIER=' \
  'TEMPORARY_WEB_PIN_VERIFIER_FILE=' \
  'TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=' \
  'TEMPORARY_WEB_PIN_EXPIRES_AT=' \
  'WHATSAPP_BACKEND=disabled' \
  'UNRELATED_SETTING=preserved' > "$env_file"
chmod 0600 "$env_file"
env_uid_before="$(stat -c '%u' "$env_file" 2>/dev/null || stat -f '%u' "$env_file")"
env_gid_before="$(stat -c '%g' "$env_file" 2>/dev/null || stat -f '%g' "$env_file")"

rewrite_release_pointers "$env_file" \
  "$new_revision" "sha-$new_revision" "$new_infra" \
  "$new_api" "$new_web" "$new_caddy" "$new_postgres" "$new_backup" "$new_hermes"

test "$(release_file_mode "$env_file")" = 600
test "$(stat -c '%u' "$env_file" 2>/dev/null || stat -f '%u' "$env_file")" = "$env_uid_before"
test "$(stat -c '%g' "$env_file" 2>/dev/null || stat -f '%g' "$env_file")" = "$env_gid_before"
grep -Fxq 'JWT_SECRET=preserve=this=value exactly' "$env_file"
grep -Fxq 'UNRELATED_SETTING=preserved' "$env_file"
grep -Fxq "DEPLOY_REF=$new_revision" "$env_file"
grep -Fxq "IMAGE_TAG=sha-$new_revision" "$env_file"
grep -Fxq "INFRA_IMAGE_TAG=$new_infra" "$env_file"
grep -Fxq "API_IMAGE=$new_api" "$env_file"
grep -Fxq "WEB_IMAGE=$new_web" "$env_file"
grep -Fxq "CADDY_IMAGE=$new_caddy" "$env_file"
grep -Fxq "POSTGRES_IMAGE=$new_postgres" "$env_file"
grep -Fxq "BACKUP_IMAGE=$new_backup" "$env_file"
grep -Fxq "HERMES_IMAGE=$new_hermes" "$env_file"
test "$(grep -Ec '^(DEPLOY_REF|IMAGE_TAG|INFRA_IMAGE_TAG|API_IMAGE|WEB_IMAGE|CADDY_IMAGE|POSTGRES_IMAGE|BACKUP_IMAGE|HERMES_IMAGE)=' "$env_file")" = 9
test -z "$(find "$test_dir" -name '.env.production.release.*' -print -quit)"

# A config-only activation and rollback reuse the same immutable images. The
# boundary helper must update pointers and every non-secret auth selector in one
# atomic rename while always forcing the legacy inline verifier blank.
rewrite_release_boundary "$env_file" \
  "$new_revision" "sha-$new_revision" "$new_infra" \
  "$new_api" "$new_web" "$new_caddy" "$new_postgres" "$new_backup" "$new_hermes" \
  temporary_static_pin /run/auth-secret/temporary_web_pin_verifier \
  "$versioned_verifier_path" \
  2099-01-01T00:00:00Z disabled
grep -Fxq 'AUTH_LOGIN_MODE=temporary_static_pin' "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER=' "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER_FILE=/run/auth-secret/temporary_web_pin_verifier' "$env_file"
grep -Fxq "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=$versioned_verifier_path" "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_EXPIRES_AT=2099-01-01T00:00:00Z' "$env_file"
grep -Fxq 'WHATSAPP_BACKEND=disabled' "$env_file"

boundary_before="$(shasum -a 256 "$env_file")"
if rewrite_release_boundary "$env_file" \
    "$new_revision" "sha-$new_revision" "$new_infra" \
    "$new_api" "$new_web" "$new_caddy" "$new_postgres" "$new_backup" "$new_hermes" \
    temporary_static_pin /run/auth-secret/temporary_web_pin_verifier \
    "$versioned_verifier_path" invalid disabled; then
  echo "An invalid auth boundary was accepted" >&2
  exit 1
fi
test "$(shasum -a 256 "$env_file")" = "$boundary_before"

rewrite_release_boundary "$env_file" \
  "$new_revision" "sha-$new_revision" "$new_infra" \
  "$new_api" "$new_web" "$new_caddy" "$new_postgres" "$new_backup" "$new_hermes" \
  disabled '' '' '' disabled
grep -Fxq 'AUTH_LOGIN_MODE=disabled' "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER=' "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER_FILE=' "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=' "$env_file"
grep -Fxq 'TEMPORARY_WEB_PIN_EXPIRES_AT=' "$env_file"

duplicate_env="$test_dir/duplicate.env"
cp "$env_file" "$duplicate_env"
printf 'API_IMAGE=%s\n' "$old_api" >> "$duplicate_env"
chmod 0600 "$duplicate_env"
duplicate_before="$(shasum -a 256 "$duplicate_env")"
if rewrite_release_pointers "$duplicate_env" \
  "$old_revision" "sha-$old_revision" "$old_infra" \
  "$old_api" "$old_web" "$old_caddy" "$old_postgres" "$old_backup" "$old_hermes"; then
  echo "Duplicate release pointers were accepted" >&2
  exit 1
fi
test "$(shasum -a 256 "$duplicate_env")" = "$duplicate_before"

missing_env="$test_dir/missing.env"
grep -v '^HERMES_IMAGE=' "$env_file" > "$missing_env"
chmod 0600 "$missing_env"
if rewrite_release_pointers "$missing_env" \
  "$old_revision" "sha-$old_revision" "$old_infra" \
  "$old_api" "$old_web" "$old_caddy" "$old_postgres" "$old_backup" "$old_hermes"; then
  echo "A missing release pointer was accepted" >&2
  exit 1
fi

missing_auth_env="$test_dir/missing-auth.env"
grep -v '^AUTH_LOGIN_MODE=' "$env_file" > "$missing_auth_env"
chmod 0600 "$missing_auth_env"
if rewrite_release_boundary "$missing_auth_env" \
    "$old_revision" "sha-$old_revision" "$old_infra" \
    "$old_api" "$old_web" "$old_caddy" "$old_postgres" "$old_backup" "$old_hermes" \
    disabled '' '' '' disabled; then
  echo "A missing auth-boundary field was accepted" >&2
  exit 1
fi

invalid_release="$test_dir/invalid-auth-release.json"
jq '.auth.login_mode = "temporary_static_pin"' "$release_file" > "$invalid_release"
chmod 0600 "$invalid_release"
if load_release_boundary "$invalid_release"; then
  echo "An inconsistent recorded auth boundary was accepted" >&2
  exit 1
fi

legacy_release="$test_dir/legacy-release.json"
jq 'del(.auth)' "$release_file" > "$legacy_release"
chmod 0600 "$legacy_release"
load_release_boundary "$legacy_release"
test "$RELEASE_AUTH_LOGIN_MODE" = disabled
test -z "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE"
test -z "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"
test -z "$RELEASE_TEMPORARY_WEB_PIN_EXPIRES_AT"
test "$RELEASE_WHATSAPP_BACKEND" = disabled

# The one-time compatibility promotion must still load the historical fixed
# host path without PIN re-entry. New setter output uses the versioned form.
legacy_path_release="$test_dir/legacy-path-release.json"
jq --arg verifier_file /run/auth-secret/temporary_web_pin_verifier \
  --arg verifier_host "$legacy_verifier_path" \
  '.auth = {
    login_mode: "temporary_static_pin",
    temporary_web_pin_verifier_file: $verifier_file,
    temporary_web_pin_verifier_file_host: $verifier_host,
    temporary_web_pin_expires_at: "2099-01-01T00:00:00Z",
    whatsapp_backend: "disabled"
  }' "$release_file" >"$legacy_path_release"
chmod 0600 "$legacy_path_release"
load_release_boundary "$legacy_path_release"
test "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" = "$legacy_verifier_path"
temporary_verifier_host_path_is_legacy "$legacy_verifier_path"
temporary_verifier_host_path_is_versioned "$versioned_verifier_path"

chmod 0644 "$release_file"
if load_release_boundary "$release_file"; then
  echo "A non-private release record was accepted" >&2
  exit 1
fi

echo "Release boundary persistence contract passed"
