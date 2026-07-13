#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launcher="$ROOT_DIR/infra/bin/bumpabestie-promote"
if ! command -v flock >/dev/null 2>&1; then
  echo 'Stable promotion coordinator contract skipped: flock is unavailable.'
  exit 0
fi
tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
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
    web:$image,caddy:$image,postgres:$image,redis:("redis@"+$image|split("@")[1]),
    backup:$image,hermes:$image}}' >"$repo/.deployed-release.json"
  printf 'now %s sha-%s sha-%s\n' \
    "$previous_revision" "$previous_revision" "$previous_revision" >"$repo/.deployed-revision"
  for pair in DEPLOY_REF:$previous_revision IMAGE_TAG:sha-$previous_revision \
    INFRA_IMAGE_TAG:sha-$previous_revision API_IMAGE:$image WEB_IMAGE:$image \
    CADDY_IMAGE:$image POSTGRES_IMAGE:$image BACKUP_IMAGE:$image HERMES_IMAGE:$image; do
    printf '%s=%s\n' "${pair%%:*}" "${pair#*:}"
  done >"$repo/.env.production"
  chmod 0600 "$repo/.env.production" "$repo/.deployed-release.json" "$repo/.deployed-revision"
}

commit_target_bundle() {
  local body="$1"
  git -C "$repo" checkout -q -B target
  mkdir -p "$repo/scripts"
  printf '%s\n' '#!/usr/bin/env bash' 'set -Eeuo pipefail' "$body" \
    >"$repo/scripts/promote_release.sh"
  for helper in maintenance_lock.sh release_boundary.sh promotion_state.sh; do
    printf '%s\n' '#!/usr/bin/env bash' ': target-helper-contract' >"$repo/scripts/$helper"
  done
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
