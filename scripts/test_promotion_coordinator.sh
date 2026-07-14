#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launcher="$ROOT_DIR/infra/bin/bumpabestie-promote"
if ! command -v flock >/dev/null 2>&1; then
  echo 'Stable promotion coordinator contract skipped: flock is unavailable.'
  exit 0
fi
tmp="$(mktemp -d)"
cleanup() {
  if [[ "${KEEP_PROMOTION_CONTRACT_TMP:-0}" == "1" ]]; then
    printf 'Promotion contract fixtures retained at %s\n' "$tmp" >&2
  else
    rm -rf "$tmp"
  fi
}
trap cleanup EXIT

repo="$tmp/repo"
origin="$tmp/origin.git"
state="$tmp/state"
git init -q --bare "$origin"
git init -q "$repo"
git -C "$repo" config user.name contract
git -C "$repo" config user.email contract@example.test
git -C "$repo" remote add origin "$origin"
printf '%s\n' .env.production .deployed-release.json .deployed-revision >"$repo/.gitignore"
printf 'base\n' >"$repo/tracked"
git -C "$repo" add .gitignore tracked
git -C "$repo" commit -qm base
previous_revision="$(git -C "$repo" rev-parse HEAD)"
mkdir -p "$state"
chmod 0700 "$state"

digest='sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
image="ghcr.io/example/image@$digest"

write_boundary() {
  jq -n --arg revision "$previous_revision" --arg image "$image" '{
    revision:$revision, operations_revision:$revision, image_tag:("sha-"+$revision),
    infra_image_tag:("sha-"+$revision), images:{api:$image,worker:$image,scheduler:$image,
    web:$image,caddy:$image,postgres:$image,redis:("redis@"+($image|split("@")[1])),
    backup:$image,hermes:$image}, auth:{login_mode:"disabled",
    temporary_web_pin_verifier_file:"",temporary_web_pin_verifier_file_host:"",
    temporary_web_pin_expires_at:"",whatsapp_backend:"disabled"}}' \
    >"$repo/.deployed-release.json"
  printf 'now %s sha-%s sha-%s\n' \
    "$previous_revision" "$previous_revision" "$previous_revision" >"$repo/.deployed-revision"
  for pair in DEPLOY_REF:$previous_revision IMAGE_TAG:sha-$previous_revision \
    INFRA_IMAGE_TAG:sha-$previous_revision API_IMAGE:$image WEB_IMAGE:$image \
    CADDY_IMAGE:$image POSTGRES_IMAGE:$image BACKUP_IMAGE:$image HERMES_IMAGE:$image; do
    printf '%s=%s\n' "${pair%%:*}" "${pair#*:}"
  done >"$repo/.env.production"
  printf '%s\n' \
    'AUTH_LOGIN_MODE=disabled' \
    'TEMPORARY_WEB_PIN_VERIFIER=' \
    'TEMPORARY_WEB_PIN_VERIFIER_FILE=' \
    'TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=' \
    'TEMPORARY_WEB_PIN_EXPIRES_AT=' \
    'WHATSAPP_BACKEND=disabled' >>"$repo/.env.production"
  chmod 0600 "$repo/.env.production" "$repo/.deployed-release.json" "$repo/.deployed-revision"
}

stage_temporary_auth_activation() {
  local auth_env_tmp
  auth_env_tmp="$(mktemp "$repo/.env.production.phase-two.XXXXXX")"
  awk -F= '
    $1 == "AUTH_LOGIN_MODE" { print "AUTH_LOGIN_MODE=temporary_static_pin"; next }
    $1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE" {
      print "TEMPORARY_WEB_PIN_VERIFIER_FILE=/run/auth-secret/temporary_web_pin_verifier"; next
    }
    $1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" {
      print "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier"; next
    }
    $1 == "TEMPORARY_WEB_PIN_EXPIRES_AT" {
      print "TEMPORARY_WEB_PIN_EXPIRES_AT=2099-01-01T00:00:00Z"; next
    }
    { print }
  ' "$repo/.env.production" >"$auth_env_tmp"
  chmod 0600 "$auth_env_tmp"
  mv -f "$auth_env_tmp" "$repo/.env.production"
}

commit_target_bundle() {
  local body="$1"
  local helper_mode="${2:-stub}"
  git -C "$repo" checkout -q -B target
  mkdir -p "$repo/scripts"
  printf '%s\n' '#!/usr/bin/env bash' 'set -Eeuo pipefail' "$body" \
    >"$repo/scripts/promote_release.sh"
  if [[ "$helper_mode" == "release-boundary" ]]; then
    cp "$ROOT_DIR/scripts/release_boundary.sh" "$repo/scripts/release_boundary.sh"
    for helper in maintenance_lock.sh promotion_state.sh; do
      printf '%s\n' '#!/usr/bin/env bash' ': target-helper-contract' >"$repo/scripts/$helper"
    done
  else
    for helper in maintenance_lock.sh release_boundary.sh promotion_state.sh; do
      printf '%s\n' '#!/usr/bin/env bash' ': target-helper-contract' >"$repo/scripts/$helper"
    done
  fi
  chmod 0755 "$repo/scripts/"*.sh
  git -C "$repo" add scripts
  git -C "$repo" commit -qm target
  target_revision="$(git -C "$repo" rev-parse HEAD)"
  git -C "$repo" push -q --force origin HEAD:main
  git -C "$repo" checkout -q --detach "$previous_revision"
}

write_boundary
# The target script body is deliberately passed literally for later execution.
# shellcheck disable=SC2016
commit_target_bundle \
  'test "$BUMPABESTIE_STABLE_COORDINATOR" = 1; test -f "$BUMPABESTIE_COORDINATOR_JOURNAL"; test -n "$BUMPABESTIE_MAINTENANCE_LOCK_FD"; exit 42'

# The target worker is extracted from origin/main; the prior checkout does not
# need to contain promotion code. An unmodified failure is exactly restored.
test ! -e "$repo/scripts/promote_release.sh"
set +e
BUMPABESTIE_REPOSITORY="$repo" BUMPABESTIE_STATE_DIRECTORY="$state" \
  "$launcher" "$target_revision" "sha-$target_revision" \
  "$image" "$image" "$image" "$image" "$image" "$image"
result=$?
set -e
test "$result" = 42
test "$(git -C "$repo" rev-parse HEAD)" = "$previous_revision"
test ! -e "$state/maintenance.lock.coordinator-state.json"
test "$(find "$state/promotion-history" -name '*-PREVIOUS_RESTORED.json' | wc -l | tr -d ' ')" = 1

# Phase-two activation reuses the exact revision and all image digests. A
# forced child failure must restore the disabled auth boundary recorded by
# phase one, and the stable coordinator must recognize that canonical state.
# shellcheck disable=SC2016
commit_target_bundle \
  'source "$BUMPABESTIE_HELPER_DIR/release_boundary.sh"; load_release_boundary "$BUMPABESTIE_ROOT_DIR/.deployed-release.json"; rewrite_release_boundary "$BUMPABESTIE_ROOT_DIR/.env.production" "$RELEASE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_INFRA_IMAGE_TAG" "$RELEASE_API_IMAGE" "$RELEASE_WEB_IMAGE" "$RELEASE_CADDY_IMAGE" "$RELEASE_POSTGRES_IMAGE" "$RELEASE_BACKUP_IMAGE" "$RELEASE_HERMES_IMAGE" "$RELEASE_AUTH_LOGIN_MODE" "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE" "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" "$RELEASE_TEMPORARY_WEB_PIN_EXPIRES_AT" "$RELEASE_WHATSAPP_BACKEND"; exit 46' \
  release-boundary
previous_revision="$target_revision"
git -C "$repo" checkout -q --detach "$previous_revision"
write_boundary
stage_temporary_auth_activation
set +e
BUMPABESTIE_REPOSITORY="$repo" BUMPABESTIE_STATE_DIRECTORY="$state" \
  "$launcher" "$target_revision" "sha-$target_revision" \
  "$image" "$image" "$image" "$image" "$image" "$image"
result=$?
set -e
test "$result" = 46
test "$(git -C "$repo" rev-parse HEAD)" = "$previous_revision"
grep -Fxq 'AUTH_LOGIN_MODE=disabled' "$repo/.env.production"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER_FILE=' "$repo/.env.production"
grep -Fxq 'TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=' "$repo/.env.production"
grep -Fxq 'TEMPORARY_WEB_PIN_EXPIRES_AT=' "$repo/.env.production"
test "$(find "$state/promotion-history" -name '*-PREVIOUS_RESTORED.json' | wc -l | tr -d ' ')" = 2
for history in "$state"/promotion-history/*-PREVIOUS_RESTORED.json; do
  jq --exit-status '
    .previous_auth == {
      login_mode: "disabled",
      temporary_web_pin_verifier_file: "",
      temporary_web_pin_verifier_file_host: "",
      temporary_web_pin_expires_at: "",
      whatsapp_backend: "disabled"
    }
  ' "$history" >/dev/null
done

# A failure before the target child starts must restore the exact canonical
# prior environment itself; no target helper exists yet to perform restoration.
prior_environment_sha256="$(sha256sum "$repo/.env.production" | awk '{print $1}')"
commit_target_bundle 'exit 99'
stage_temporary_auth_activation
set +e
BUMPABESTIE_REPOSITORY="$repo" BUMPABESTIE_STATE_DIRECTORY="$state" \
  "$launcher" "$previous_revision" "sha-$previous_revision" \
  "$image" "$image" "$image" "$image" "$image" "$image"
result=$?
set -e
test "$result" = 2
test "$(sha256sum "$repo/.env.production" | awk '{print $1}')" = \
  "$prior_environment_sha256"
test "$(git -C "$repo" rev-parse HEAD)" = "$previous_revision"
test ! -e "$state/maintenance.lock.coordinator-state.json"
test ! -e "$state/maintenance.lock.maintenance-required"
test "$(find "$state/promotion-history" -name '*-PREVIOUS_RESTORED.json' | wc -l | tr -d ' ')" = 3

# A target worker that partially mutates release state can never be labelled
# restored. The stable journal and maintenance interlock survive, and a later
# invocation fails closed before reading target code.
# shellcheck disable=SC2016
commit_target_bundle \
  'printf "tampered\\n" >>"$BUMPABESTIE_ROOT_DIR/.env.production"; exit 44'
write_boundary
set +e
BUMPABESTIE_REPOSITORY="$repo" BUMPABESTIE_STATE_DIRECTORY="$state" \
  "$launcher" "$target_revision" "sha-$target_revision" \
  "$image" "$image" "$image" "$image" "$image" "$image"
result=$?
set -e
test "$result" = 78
test -f "$state/maintenance.lock.coordinator-state.json"
test -f "$state/maintenance.lock.maintenance-required"

set +e
BUMPABESTIE_REPOSITORY="$repo" BUMPABESTIE_STATE_DIRECTORY="$state" \
  "$launcher" "$target_revision" "sha-$target_revision" \
  "$image" "$image" "$image" "$image" "$image" "$image"
result=$?
set -e
test "$result" = 78

echo 'Stable promotion coordinator contract passed.'
