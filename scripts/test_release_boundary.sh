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
    }
  }' > "$release_file"
chmod 0600 "$release_file"
load_release_boundary "$release_file"
test "$RELEASE_REVISION" = "$old_revision"
test "$RELEASE_OPERATIONS_REVISION" = "$old_revision"
test "$RELEASE_API_IMAGE" = "$old_api"
test "$RELEASE_BACKUP_IMAGE" = "$old_backup"
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

chmod 0644 "$release_file"
if load_release_boundary "$release_file"; then
  echo "A non-private release record was accepted" >&2
  exit 1
fi

echo "Release boundary persistence contract passed"
