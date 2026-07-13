#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${BUMPABESTIE_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HELPER_DIR="${BUMPABESTIE_HELPER_DIR:-$ROOT_DIR/scripts}"
cd "$ROOT_DIR"

if [[ "${BUMPABESTIE_STABLE_COORDINATOR:-}" != "1" \
  || -z "${BUMPABESTIE_COORDINATOR_JOURNAL:-}" \
  || ! -f "$BUMPABESTIE_COORDINATOR_JOURNAL" \
  || -L "$BUMPABESTIE_COORDINATOR_JOURNAL" ]]; then
  echo "Release promotion must be launched by the installed stable coordinator" >&2
  exit 2
fi
coordinator_mode="$(stat -c '%a' "$BUMPABESTIE_COORDINATOR_JOURNAL" 2>/dev/null \
  || stat -f '%Lp' "$BUMPABESTIE_COORDINATOR_JOURNAL")"
if [[ "$coordinator_mode" != "600" ]] \
  || ! jq --exit-status '.schema_version == 1 and .phase == "CHILD_RUNNING"' \
    "$BUMPABESTIE_COORDINATOR_JOURNAL" >/dev/null; then
  echo "Stable coordinator journal is invalid" >&2
  exit 2
fi
for helper in maintenance_lock.sh release_boundary.sh promotion_state.sh; do
  if [[ ! -f "$HELPER_DIR/$helper" || -L "$HELPER_DIR/$helper" ]]; then
    echo "Stable coordinator helper bundle is incomplete" >&2
    exit 2
  fi
done

# The stable coordinator supplies a private, revision-pinned helper directory.
# shellcheck source=/dev/null
source "$HELPER_DIR/maintenance_lock.sh"
acquire_maintenance_lock
# shellcheck source=/dev/null
source "$HELPER_DIR/release_boundary.sh"
# shellcheck source=/dev/null
source "$HELPER_DIR/promotion_state.sh"
assert_maintenance_clear

if (($# != 8)); then
  echo "Usage: $0 REVISION INFRA_IMAGE_TAG API WEB CADDY POSTGRES BACKUP HERMES" >&2
  exit 2
fi
revision="$1"
infra_image_tag="$2"
api_image="$3"
web_image="$4"
caddy_image="$5"
postgres_image="$6"
backup_image="$7"
hermes_image="$8"

validate_release_pointer_values \
  "$revision" "sha-$revision" "$infra_image_tag" \
  "$api_image" "$web_image" "$caddy_image" "$postgres_image" \
  "$backup_image" "$hermes_image" || {
    echo "Promotion requires a full revision, immutable infrastructure tag and six digest references" >&2
    exit 2
  }

original_checkout="$(git rev-parse HEAD)"

prior_boundary_loaded=0
promotion_state_file="${BUMPABESTIE_MAINTENANCE_LOCK:-/var/lib/bumpabestie/maintenance.lock}.promotion-state.$$"
restore_promotion() {
  local result=$?
  local phase=""
  trap - EXIT
  phase="$(read_promotion_state "$promotion_state_file" 2>/dev/null || true)"
  if ((result != 0)) && [[ "$phase" == "PRE_BOUNDARY" ]]; then
    if ((prior_boundary_loaded)) \
      && rewrite_release_pointers .env.production \
        "$prior_revision" "$prior_image_tag" "$prior_infra_image_tag" \
        "$prior_api_image" "$prior_web_image" "$prior_caddy_image" \
        "$prior_postgres_image" "$prior_backup_image" "$prior_hermes_image" \
      && git checkout --detach "$original_checkout" >/dev/null 2>&1; then
      if write_promotion_state "$promotion_state_file" PREVIOUS_RESTORED; then
        phase="PREVIOUS_RESTORED"
      else
        mark_maintenance_required "launcher_terminal_state_write_failed" || true
        phase="STATE_WRITE_FAILED"
      fi
    else
      mark_maintenance_required "launcher_preboundary_restore_failed" || true
      phase="RESTORE_FAILED"
    fi
  fi
  case "$phase" in
    PREVIOUS_RESTORED | HYBRID_PERSISTED | COMMITTED)
      rm -f -- "$promotion_state_file"
      ;;
    "")
      if ((result != 0 && prior_boundary_loaded)); then
        # No target deploy ran yet, so the launcher still owns restoration.
        rewrite_release_pointers .env.production \
          "$prior_revision" "$prior_image_tag" "$prior_infra_image_tag" \
          "$prior_api_image" "$prior_web_image" "$prior_caddy_image" \
          "$prior_postgres_image" "$prior_backup_image" "$prior_hermes_image" || true
        git checkout --detach "$original_checkout" >/dev/null 2>&1 || true
      elif ((result == 0)); then
        mark_maintenance_required "promotion_state=missing_after_success" || true
        result=1
      fi
      ;;
    *)
      mark_maintenance_required "promotion_state=$phase" || true
      result=1
      ;;
  esac
  exit "$result"
}
trap restore_promotion EXIT

if [[ ! -f .env.production || -L .env.production ]] \
  || [[ "$(release_file_mode .env.production)" != "600" ]]; then
  echo ".env.production must be a private regular file" >&2
  exit 2
fi
if ! load_release_boundary .deployed-release.json; then
  echo "A valid private .deployed-release.json is required for promotion" >&2
  exit 2
fi

prior_revision="$RELEASE_REVISION"
prior_image_tag="$RELEASE_IMAGE_TAG"
prior_infra_image_tag="$RELEASE_INFRA_IMAGE_TAG"
prior_api_image="$RELEASE_API_IMAGE"
prior_web_image="$RELEASE_WEB_IMAGE"
prior_caddy_image="$RELEASE_CADDY_IMAGE"
prior_postgres_image="$RELEASE_POSTGRES_IMAGE"
prior_backup_image="$RELEASE_BACKUP_IMAGE"
prior_hermes_image="$RELEASE_HERMES_IMAGE"
prior_operations_revision="$RELEASE_OPERATIONS_REVISION"
prior_boundary_loaded=1
if ! git rev-parse --verify "${prior_operations_revision}^{commit}" >/dev/null 2>&1; then
  echo "Recorded previous checkout is not locally available" >&2
  exit 2
fi
original_checkout="$prior_operations_revision"
write_promotion_state "$promotion_state_file" PRE_BOUNDARY

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing promotion from a dirty checkout" >&2
  exit 2
fi
git fetch --tags --prune origin main
git rev-parse --verify "${revision}^{commit}" >/dev/null
if [[ "$(git rev-parse origin/main)" != "$revision" ]]; then
  echo "Promotion revision must be the fetched origin/main commit" >&2
  exit 2
fi
git checkout --detach "$revision"
if [[ "$(git rev-parse HEAD)" != "$revision" ]]; then
  echo "Target checkout verification failed" >&2
  exit 2
fi

# Source the reviewed target implementation after checkout; a promotion must
# never use stale pointer validation from the previously deployed release.
# shellcheck source=scripts/release_boundary.sh
source "$ROOT_DIR/scripts/release_boundary.sh"
# ShellCheck models the EXIT trap body as a subshell and cannot track the
# immutable positional values into this post-checkout call.
# shellcheck disable=SC2031
rewrite_release_pointers .env.production \
  "$revision" "sha-$revision" "$infra_image_tag" \
  "$api_image" "$web_image" "$caddy_image" "$postgres_image" \
  "$backup_image" "$hermes_image"

set +e
env \
  BUMPABESTIE_PREVIOUS_CHECKOUT="$original_checkout" \
  BUMPABESTIE_PROMOTION_STATE_FILE="$promotion_state_file" \
  "$ROOT_DIR/scripts/deploy.sh"
deploy_result=$?
set -e
if ((deploy_result != 0)); then
  exit "$deploy_result"
fi
