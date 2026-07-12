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
  DEPLOY_REF IMAGE_TAG INFRA_IMAGE_TAG \
  API_IMAGE WEB_IMAGE CADDY_IMAGE POSTGRES_IMAGE BACKUP_IMAGE \
  APP_DOMAIN WWW_DOMAIN ADMIN_DOMAIN RESEARCH_DOMAIN API_DOMAIN \
  WHATSAPP_BACKEND BUMPA_BACKEND AGENT_BACKEND; do
  value="$(value_for "$key")"
  printf -v "$key" '%s' "$value"
  export "${key?}"
done

if [[ -z "${DEPLOY_REF:-}" || -z "${IMAGE_TAG:-}" || -z "${INFRA_IMAGE_TAG:-}" ]]; then
  echo "DEPLOY_REF, IMAGE_TAG and INFRA_IMAGE_TAG are required" >&2
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
git checkout --detach "$deploy_commit"

compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)

running_image() {
  local service="$1"
  local container_id configured_ref image_id repository immutable_ref last_component
  container_id="$("${compose[@]}" ps --status running -q "$service")"
  if [[ -n "$container_id" ]]; then
    configured_ref="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
    if [[ "$configured_ref" =~ @sha256:[a-f0-9]{64}$ ]]; then
      printf '%s\n' "$configured_ref"
      return
    fi

    repository="$configured_ref"
    last_component="${repository##*/}"
    if [[ "$last_component" == *:* ]]; then
      repository="${repository%:*}"
    fi
    image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
    immutable_ref="$(
      docker image inspect --format '{{json .RepoDigests}}' "$image_id" |
        jq --raw-output --arg prefix "$repository@sha256:" \
          '[.[] | select(startswith($prefix))][0] // ""'
    )"
    if [[ ! "$immutable_ref" =~ @sha256:[a-f0-9]{64}$ ]]; then
      echo "Unable to resolve an immutable rollback image for running $service" >&2
      return 1
    fi
    printf '%s\n' "$immutable_ref"
  fi
}

previous_api_image="$(running_image api)"
previous_web_image="$(running_image web)"
previous_writer_containers=()
for service in api web worker scheduler caddy; do
  container_id="$("${compose[@]}" ps --status running -q "$service")"
  if [[ -n "$container_id" ]]; then
    previous_writer_containers+=("$container_id")
  fi
done
previous_application_revision=""
if [[ -n "$previous_api_image" ]]; then
  previous_application_revision="$(
    docker image inspect \
      --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
      "$previous_api_image" 2>/dev/null || true
  )"
fi

deployment_started=0
writers_quiesced=0
rollback() {
  result=$?
  trap - EXIT
  if ((result == 0)); then
    return
  fi

  echo "Deployment of $deploy_commit failed." >&2
  rm -f .deployed-revision.tmp.* .deployed-release.json.tmp.*
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --no-color --tail=200 caddy api web postgres redis >&2 || true

  if ((writers_quiesced && !deployment_started && ${#previous_writer_containers[@]} > 0)); then
    echo "Restarting the previously running application after pre-deployment failure." >&2
    set +e
    docker start "${previous_writer_containers[@]}" >/dev/null
    SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh
    restart_result=$?
    set -e
    if ((restart_result == 0)); then
      echo "The previous application resumed successfully." >&2
    else
      echo "The previous application did not recover cleanly; operator intervention is required." >&2
    fi
  elif ((deployment_started)) \
    && [[ -n "$previous_api_image" && -n "$previous_web_image" ]]; then
    echo "Attempting API and web rollback while retaining forward-only infrastructure." >&2
    set +e
    export API_IMAGE="$previous_api_image"
    export WEB_IMAGE="$previous_web_image"
    "${compose[@]}" pull web api
    "${compose[@]}" up --no-deps --force-recreate \
      --abort-on-container-exit --exit-code-from caddy-init caddy-init
    "${compose[@]}" up -d --no-deps api web caddy
    SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh
    rollback_result=$?
    set -e
    if ((rollback_result == 0)); then
      echo "Application rollback succeeded; edge and data services remained forward-only." >&2
    else
      echo "Application rollback also failed; operator intervention is required." >&2
    fi
  else
    echo "No previously verified release is available for automatic rollback." >&2
  fi
  exit "$result"
}
trap rollback EXIT

"${compose[@]}" --profile tools pull caddy postgres redis web api backup

verify_image_revision() {
  local image="$1"
  local expected_revision="$2"
  local service="$3"
  local actual_revision
  actual_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
  if [[ "$actual_revision" != "$expected_revision" ]]; then
    echo "$service image revision mismatch" >&2
    exit 1
  fi
}

infra_commit="${INFRA_IMAGE_TAG#sha-}"
verify_image_revision "$API_IMAGE" "$deploy_commit" api
verify_image_revision "$WEB_IMAGE" "$deploy_commit" web
verify_image_revision "$CADDY_IMAGE" "$infra_commit" caddy
verify_image_revision "$POSTGRES_IMAGE" "$infra_commit" postgres
verify_image_revision "$BACKUP_IMAGE" "$infra_commit" backup

target_pg_version="$(docker run --rm --entrypoint postgres "$POSTGRES_IMAGE" --version)"
target_pg_version="${target_pg_version##* }"
if [[ ! "$target_pg_version" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
  echo "Unable to parse PostgreSQL target binary version" >&2
  exit 1
fi
target_pg_major="${BASH_REMATCH[1]}"
target_pg_minor="${BASH_REMATCH[2]}"
target_pg_version_num="$((10#$target_pg_major * 10000 + 10#$target_pg_minor))"
if [[ "$target_pg_major" != "16" ]]; then
  echo "Only PostgreSQL major 16 is approved; target image reports $target_pg_major" >&2
  exit 1
fi

postgres_container="$("${compose[@]}" ps --status running -q postgres)"
writers_quiesced=1
"${compose[@]}" stop --timeout 60 caddy web api worker scheduler
if [[ -n "$postgres_container" ]]; then
  running_pg_version_num="$(
    docker exec "$postgres_container" psql \
      --username "$(value_for POSTGRES_USER)" \
      --dbname "$(value_for POSTGRES_DB)" \
      --tuples-only --no-align --command 'SHOW server_version_num'
  )"
  if [[ "${running_pg_version_num:0:2}" != "$target_pg_major" ]]; then
    echo "Refusing an in-place PostgreSQL major-version change" >&2
    exit 1
  fi
  if ((10#$target_pg_version_num < 10#$running_pg_version_num)); then
    echo "Refusing to downgrade PostgreSQL from $running_pg_version_num to $target_pg_version_num" >&2
    exit 1
  fi
  legacy_brin_indexes="$(
    docker exec "$postgres_container" psql \
      --username "$(value_for POSTGRES_USER)" \
      --dbname "$(value_for POSTGRES_DB)" \
      --tuples-only --no-align \
      --command "SELECT count(*) FROM pg_indexes WHERE indexdef LIKE '%numeric_minmax_multi_ops%'"
  )"
  if [[ "$legacy_brin_indexes" != "0" ]]; then
    echo "PostgreSQL contains BRIN numeric_minmax_multi_ops indexes requiring reviewed REINDEX work" >&2
    exit 1
  fi
  non_core_extensions="$(
    docker exec "$postgres_container" psql \
      --username "$(value_for POSTGRES_USER)" \
      --dbname "$(value_for POSTGRES_DB)" \
      --tuples-only --no-align \
      --command "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname"
  )"
  if [[ -n "$non_core_extensions" ]]; then
    echo "PostgreSQL has non-core extensions requiring an explicit compatibility review" >&2
    exit 1
  fi
  echo "Creating a pre-deployment database and artifact backup."
  "${compose[@]}" --profile tools run --rm --no-deps \
    --env "APPLICATION_REVISION=${previous_application_revision:-unknown}" \
    --env "BACKUP_IMAGE_REF=$BACKUP_IMAGE" \
    backup
fi

deployment_started=1
"${compose[@]}" rm -f worker scheduler 2>/dev/null || true
"${compose[@]}" up -d --wait --wait-timeout 180 postgres redis
postgres_container="$("${compose[@]}" ps --status running -q postgres)"
docker exec \
  --env "POSTGRES_USER=$(value_for POSTGRES_USER)" \
  --env "POSTGRES_DB=$(value_for POSTGRES_DB)" \
  --env "APP_POSTGRES_PASSWORD=$(value_for APP_POSTGRES_PASSWORD)" \
  "$postgres_container" /docker-entrypoint-initdb.d/10-app-role.sh
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d --wait --wait-timeout 240 --remove-orphans api web caddy
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

deployed_at="$(date -u +%FT%TZ)"
revision_tmp=".deployed-revision.tmp.$$"
release_tmp=".deployed-release.json.tmp.$$"
printf '%s %s %s %s\n' "$deployed_at" "$deploy_commit" "$IMAGE_TAG" "$INFRA_IMAGE_TAG" > "$revision_tmp"

running_image_ref() {
  local service="$1"
  local container_id configured_ref
  container_id="$("${compose[@]}" ps -q "$service")"
  configured_ref="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
  if [[ ! "$configured_ref" =~ @sha256:[a-f0-9]{64}$ ]]; then
    echo "$service is not running from an immutable digest reference" >&2
    return 1
  fi
  printf '%s\n' "$configured_ref"
}

jq --null-input \
  --arg deployed_at "$deployed_at" \
  --arg revision "$deploy_commit" \
  --arg image_tag "$IMAGE_TAG" \
  --arg infra_image_tag "$INFRA_IMAGE_TAG" \
  --arg api "$(running_image_ref api)" \
  --arg web "$(running_image_ref web)" \
  --arg caddy "$(running_image_ref caddy)" \
  --arg postgres "$(running_image_ref postgres)" \
  --arg redis "$(running_image_ref redis)" \
  --arg backup "$BACKUP_IMAGE" \
  '{
    deployed_at: $deployed_at,
    revision: $revision,
    image_tag: $image_tag,
    infra_image_tag: $infra_image_tag,
    images: {
      api: $api,
      web: $web,
      caddy: $caddy,
      postgres: $postgres,
      redis: $redis,
      backup: $backup
    }
  }' > "$release_tmp"
chmod 0600 "$revision_tmp" "$release_tmp"
mv "$release_tmp" .deployed-release.json
mv "$revision_tmp" .deployed-revision

trap - EXIT
echo "Deployed $deploy_commit with app tag $IMAGE_TAG and infrastructure tag $INFRA_IMAGE_TAG"
