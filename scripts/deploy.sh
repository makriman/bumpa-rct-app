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

value_for() {
  local key="$1"
  awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' .env.production
}
for key in \
  DEPLOY_REF IMAGE_TAG \
  APP_DOMAIN WWW_DOMAIN ADMIN_DOMAIN RESEARCH_DOMAIN API_DOMAIN \
  WHATSAPP_BACKEND BUMPA_BACKEND AGENT_BACKEND; do
  value="$(value_for "$key")"
  printf -v "$key" '%s' "$value"
  export "${key?}"
done

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
previous_ref=""
if [[ -f .deployed-revision ]]; then
  read -r _deployed_at previous_ref _previous_image < .deployed-revision || true
  if ! git cat-file -e "${previous_ref}^{commit}" 2>/dev/null; then
    echo "Ignoring invalid previous deployment record: ${previous_ref:-empty}" >&2
    previous_ref=""
  fi
fi
git checkout --detach "$deploy_commit"

compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)
deployment_started=0
rollback() {
  result=$?
  trap - EXIT
  if ((result == 0)); then
    return
  fi

  echo "Deployment of $deploy_commit failed." >&2
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --no-color --tail=200 caddy api web postgres redis >&2 || true

  if ((deployment_started)) && [[ -n "$previous_ref" && "$previous_ref" != "$deploy_commit" ]]; then
    echo "Attempting application rollback to $previous_ref without downgrading the database." >&2
    set +e
    git checkout --detach "$previous_ref"
    export IMAGE_TAG="sha-$previous_ref"
    "${compose[@]}" pull web api
    "${compose[@]}" up -d --remove-orphans caddy postgres redis api web
    SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh
    rollback_result=$?
    set -e
    if ((rollback_result == 0)); then
      echo "Rollback to $previous_ref succeeded." >&2
    else
      echo "Rollback to $previous_ref also failed; operator intervention is required." >&2
    fi
  else
    echo "No previously verified release is available for automatic rollback." >&2
  fi
  exit "$result"
}
trap rollback EXIT

"${compose[@]}" pull caddy postgres redis web api
if [[ -n "$("${compose[@]}" ps --status running -q postgres)" ]]; then
  echo "Creating a pre-deployment database and artifact backup."
  "${compose[@]}" --profile tools run --rm backup
fi
deployment_started=1
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" stop worker scheduler 2>/dev/null || true
"${compose[@]}" rm -f worker scheduler 2>/dev/null || true
"${compose[@]}" up -d --remove-orphans caddy postgres redis api web
SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh

for service in caddy postgres redis api web; do
  container_id="$("${compose[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "Required service has no container: $service" >&2
    exit 1
  fi
  state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "$container_id")"
  if [[ "$state" != "running" || "$restarts" != "0" ]]; then
    echo "$service is not stable: state=$state restarts=$restarts" >&2
    exit 1
  fi
done
for service in postgres redis api web; do
  container_id="$("${compose[@]}" ps -q "$service")"
  health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id")"
  if [[ "$health" != "healthy" ]]; then
    echo "$service is not healthy: $health" >&2
    exit 1
  fi
done

ready_payload="$(curl --fail --silent --show-error "https://${API_DOMAIN}/health/ready")"
jq --exit-status \
  --arg whatsapp "$WHATSAPP_BACKEND" \
  --arg bumpa "$BUMPA_BACKEND" \
  --arg agent "$AGENT_BACKEND" \
  '.status == "ready" and .database == "ok" and
   .providers.whatsapp == $whatsapp and
   .providers.bumpa == $bumpa and
   .providers.agent == $agent' <<<"$ready_payload" >/dev/null

printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$deploy_commit" "$IMAGE_TAG" > .deployed-revision

trap - EXIT
echo "Deployed $deploy_commit with image tag $IMAGE_TAG"
