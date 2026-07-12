#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env.production ]]; then
  echo ".env.production is required and must have mode 0600" >&2
  exit 2
fi
permissions="$(stat -c '%a' .env.production 2>/dev/null || stat -f '%Lp' .env.production)"
if [[ "$permissions" != "600" ]]; then
  echo ".env.production permissions must be 0600; found $permissions" >&2
  exit 2
fi
./scripts/validate_env.sh .env.production production

set -a
# shellcheck disable=SC1091
. ./.env.production
set +a

if [[ -z "${DEPLOY_REF:-}" || -z "${IMAGE_TAG:-}" || "$IMAGE_TAG" == "latest" ]]; then
  echo "DEPLOY_REF and an immutable IMAGE_TAG are required; latest is forbidden" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing deployment from a dirty checkout" >&2
  exit 2
fi

git fetch --tags --prune origin
git rev-parse --verify "${DEPLOY_REF}^{commit}" >/dev/null
deploy_commit="$(git rev-parse "${DEPLOY_REF}^{commit}")"
if [[ "$IMAGE_TAG" != "sha-$deploy_commit" ]]; then
  echo "IMAGE_TAG must be sha-$deploy_commit for DEPLOY_REF=$DEPLOY_REF" >&2
  exit 2
fi
previous_ref="$(git rev-parse HEAD)"
git checkout --detach "$deploy_commit"

compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)
rollback() {
  result=$?
  if ((result != 0)); then
    echo "Deployment failed. Application revision before deploy: $previous_ref" >&2
    "${compose[@]}" ps >&2 || true
    "${compose[@]}" logs --no-color --tail=200 api web worker scheduler >&2 || true
  fi
  exit "$result"
}
trap rollback EXIT

"${compose[@]}" pull web api worker scheduler
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d --remove-orphans caddy postgres redis api worker scheduler web
SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh
printf '%s %s\n' "$(date -u +%FT%TZ)" "$(git rev-parse HEAD)" > .deployed-revision

trap - EXIT
echo "Deployed $(git rev-parse HEAD) with image tag $IMAGE_TAG"
