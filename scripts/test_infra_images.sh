#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

suffix="${RANDOM:-0}-$$"
network="bumpabestie-infra-test-$suffix"
primary_data="bumpabestie-infra-primary-$suffix"
restore_data="bumpabestie-infra-restore-$suffix"
backup_data="bumpabestie-infra-backups-$suffix"
exports_data="bumpabestie-infra-exports-$suffix"
hermes_data="bumpabestie-infra-hermes-$suffix"
hermes_staging="bumpabestie-infra-hermes-staging-$suffix"
caddy_data="bumpabestie-infra-caddy-data-$suffix"
caddy_config="bumpabestie-infra-caddy-config-$suffix"
primary="bumpabestie-infra-primary-$suffix"
restore="bumpabestie-infra-restore-$suffix"
edge="bumpabestie-infra-edge-$suffix"
hermes_runtime="bumpabestie-infra-hermes-runtime-$suffix"
hermes_secret_dir="$(mktemp -d)"

postgres_image="bumpabestie-postgres:infra-test"
backup_image="bumpabestie-backup:infra-test"
caddy_image="bumpabestie-caddy:infra-test"
hermes_image="bumpabestie-hermes:infra-test"
legacy_postgres_image="postgres:16.9-alpine@sha256:7c688148e5e156d0e86df7ba8ae5a05a2386aaec1e2ad8e6d11bdf10504b1fb7"
database_password="infra-test-postgres-only"

cleanup() {
  result=$?
  docker rm -f "$primary" "$restore" "$edge" "$hermes_runtime" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker volume rm -f \
    "$primary_data" "$restore_data" "$backup_data" \
    "$exports_data" "$hermes_data" "$hermes_staging" \
    "$caddy_data" "$caddy_config" >/dev/null 2>&1 || true
  rm -rf "$hermes_secret_dir"
  exit "$result"
}
trap cleanup EXIT

if [[ "${SKIP_INFRA_BUILD:-false}" != "true" ]]; then
  docker build --target runtime --tag "$postgres_image" --file infra/postgres/Dockerfile .
  docker build --target backup --tag "$backup_image" --file infra/postgres/Dockerfile .
  docker build --tag "$caddy_image" --file infra/caddy/Dockerfile .
  docker build --target runtime --tag "$hermes_image" --file infra/hermes/Dockerfile .
fi

docker network create "$network" >/dev/null
for volume in \
  "$primary_data" "$restore_data" "$backup_data" "$exports_data" \
  "$hermes_data" "$hermes_staging" "$caddy_data" "$caddy_config"; do
  docker volume create "$volume" >/dev/null
done

docker run --rm \
  --volume "$exports_data:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'mkdir -p /artifacts/reports && printf expected-export > /artifacts/reports/expected.txt && chmod -R a+rX /artifacts'
docker run --rm \
  --volume "$hermes_data:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'mkdir -p /artifacts/profile && printf expected-hermes > /artifacts/profile/expected.txt && chmod -R a+rX /artifacts'

# Exercise the derived Hermes image without network access or a provider call.
# The profile fixture matches the control-plane handoff contract and contains
# only inert test credentials.
docker run --rm \
  --volume "$hermes_staging:/staged" \
  --entrypoint sh "$backup_image" -eu -c '
    profile=/staged/profiles/tenant_contract
    mkdir -p "$profile"
    : > "$profile/.no-skills"
    printf "%s\n" \
      "API_SERVER_ENABLED=true" \
      "API_SERVER_HOST=0.0.0.0" \
      "API_SERVER_PORT=8799" \
      "API_SERVER_KEY=contract-profile-key-000000000000000000000000" \
      > "$profile/.env"
    printf "%s\n" \
      "model:" \
      "  provider: anthropic" \
      "  default: claude-sonnet-5" \
      "agent:" \
      "  disabled_toolsets:" \
      "    - terminal" \
      "security:" \
      "  allow_private_urls: false" \
      > "$profile/config.yaml"
    printf "%s\n" "Contract-only isolated tenant profile." > "$profile/SOUL.md"
    printf "%s\n" "must-not-be-imported" > "$profile/unexpected.txt"
    chmod 0700 /staged /staged/profiles "$profile"
    chmod 0600 "$profile"/* "$profile"/.no-skills "$profile"/.env
  '

imported="$(
  docker run --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,exec,nosuid,nodev \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --env HERMES_STAGING_ROOT=/staged/profiles \
    --env HERMES_RUNTIME_ROOT=/opt/data/profiles \
    --volume "$hermes_staging:/staged:ro" \
    --volume "$hermes_data:/opt/data" \
    --entrypoint /usr/local/bin/bumpabestie-hermes-import \
    "$hermes_image"
)"
test "$imported" = 1
docker run --rm \
  --volume "$hermes_data:/opt/data:ro" \
  --entrypoint sh "$hermes_image" -eu -c '
    profile=/opt/data/profiles/tenant_contract
    test "$(stat -c %a "$profile")" = 700
    test "$(stat -c %a "$profile/.env")" = 600
    test "$(stat -c %U "$profile")" = hermes
    test -f "$profile/.no-skills"
    test -f "$profile/config.yaml"
    test -f "$profile/SOUL.md"
    test ! -e "$profile/unexpected.txt"
  '

docker run --rm \
  --volume "$hermes_staging:/staged" \
  --entrypoint sh "$backup_image" -eu -c '
    mkdir -p /staged/profiles/tenant_rejected
    ln -s /etc/passwd /staged/profiles/tenant_rejected/config.yaml
  '
if docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add DAC_OVERRIDE \
  --cap-add FOWNER \
  --security-opt no-new-privileges:true \
  --env HERMES_STAGING_ROOT=/staged/profiles \
  --env HERMES_RUNTIME_ROOT=/opt/data/profiles \
  --volume "$hermes_staging:/staged:ro" \
  --volume "$hermes_data:/opt/data" \
  --entrypoint /usr/local/bin/bumpabestie-hermes-import \
  "$hermes_image" >"$hermes_secret_dir/rejected.out" 2>&1; then
  echo "Hermes importer accepted a symlinked profile" >&2
  exit 1
fi
grep -Fx 'Hermes staging profiles must not contain symlinks' "$hermes_secret_dir/rejected.out" >/dev/null
docker run --rm \
  --volume "$hermes_staging:/staged" \
  --entrypoint sh "$backup_image" -c 'rm -rf /staged/profiles'
test "$(
  docker run --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,exec,nosuid,nodev \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --env HERMES_STAGING_ROOT=/staged/profiles \
    --env HERMES_RUNTIME_ROOT=/opt/data/profiles \
    --volume "$hermes_staging:/staged:ro" \
    --volume "$hermes_data:/opt/data" \
    --entrypoint /usr/local/bin/bumpabestie-hermes-import \
    "$hermes_image"
)" = 0
docker run --rm \
  --volume "$hermes_staging:/staged" \
  --entrypoint sh "$backup_image" -eu -c '
    mkdir -p /staged/control-plane
    printf "%s" expected-hermes-staging > /staged/control-plane/expected.txt
    chmod 0700 /staged /staged/control-plane
    chmod 0600 /staged/control-plane/expected.txt
  '

if docker run --rm "$hermes_image" sleep 0 >"$hermes_secret_dir/missing.out" 2>&1; then
  echo "Hermes entrypoint accepted a missing Anthropic secret" >&2
  exit 1
fi
grep -Fx 'Hermes Anthropic secret file is unavailable' "$hermes_secret_dir/missing.out" >/dev/null
printf '%s' 'invalid-contract-secret-material-000000' > "$hermes_secret_dir/invalid"
chmod 0600 "$hermes_secret_dir/invalid"
if docker run --rm \
  --volume "$hermes_secret_dir/invalid:/run/secrets/hermes_anthropic_api_key:ro" \
  "$hermes_image" sleep 0 >"$hermes_secret_dir/invalid.out" 2>&1; then
  echo "Hermes entrypoint accepted an invalid Anthropic secret" >&2
  exit 1
fi
grep -Fx 'Hermes Anthropic secret is invalid' "$hermes_secret_dir/invalid.out" >/dev/null
if grep -Fq 'invalid-contract-secret-material' "$hermes_secret_dir/invalid.out"; then
  echo "Hermes entrypoint exposed rejected secret material" >&2
  exit 1
fi

printf '%s' 'sk-ant-contract-placeholder-never-sent-000000000000000' \
  > "$hermes_secret_dir/anthropic"
chmod 0600 "$hermes_secret_dir/anthropic"
docker run --detach \
  --name "$hermes_runtime" \
  --network none \
  --read-only \
  --tmpfs /run:rw,exec,nosuid,nodev \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add DAC_OVERRIDE \
  --cap-add FOWNER \
  --cap-add SETGID \
  --cap-add SETUID \
  --security-opt no-new-privileges:true \
  --env ANTHROPIC_API_KEY_FILE=/run/secrets/hermes_anthropic_api_key \
  --env HERMES_DASHBOARD=0 \
  --volume "$hermes_secret_dir/anthropic:/run/secrets/hermes_anthropic_api_key:ro" \
  --volume "$hermes_data:/opt/data" \
  "$hermes_image" sleep infinity >/dev/null
for _attempt in {1..60}; do
  if docker exec "$hermes_runtime" sh -c \
    "/command/s6-svstat /run/service/main-hermes | grep -q '^up'"; then
    break
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "$hermes_runtime")" != true ]]; then
    docker logs "$hermes_runtime" >&2
    exit 1
  fi
  sleep 1
done
docker exec "$hermes_runtime" sh -c \
  "/command/s6-svstat /run/service/main-hermes | grep -q '^up'"
docker exec "$hermes_runtime" sh -eu -c '
  test -d /run/service/gateway-tenant_contract
  hermes -p tenant_contract gateway start >/dev/null
'
for _attempt in {1..60}; do
  if docker exec "$hermes_runtime" sh -eu -c '
    /command/s6-svstat /run/service/gateway-tenant_contract | grep -q "^up"
    curl --fail --silent --show-error \
      --header "Authorization: Bearer contract-profile-key-000000000000000000000000" \
      --max-time 3 \
      http://127.0.0.1:8799/health/detailed >/dev/null 2>&1
  '; then
    break
  fi
  sleep 1
done
docker exec "$hermes_runtime" sh -eu -c '
  /command/s6-svstat /run/service/gateway-tenant_contract | grep -q "^up"
  curl --fail --silent --show-error \
    --header "Authorization: Bearer contract-profile-key-000000000000000000000000" \
    --max-time 3 \
    http://127.0.0.1:8799/health/detailed >/dev/null
'
test "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$hermes_runtime")" = true
test "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$hermes_runtime")" = none
if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$hermes_runtime" | \
  grep -q '^ANTHROPIC_API_KEY='; then
  echo "Hermes secret leaked into Docker configuration" >&2
  exit 1
fi
docker rm -f "$hermes_runtime" >/dev/null

start_postgres() {
  local name="$1"
  local volume="$2"
  local image="$3"
  docker run --detach \
    --name "$name" \
    --network "$network" \
    --env POSTGRES_USER=bumpabestie \
    --env POSTGRES_PASSWORD="$database_password" \
    --env POSTGRES_DB=bumpabestie \
    --env APP_POSTGRES_PASSWORD=infra-app-postgres-password \
    --volume "$volume:/var/lib/postgresql/data" \
    "$image" >/dev/null

  for _attempt in {1..60}; do
    if docker exec "$name" sh -c 'test "$(cat /proc/1/comm)" = postgres' >/dev/null 2>&1 \
      && docker exec "$name" psql --username bumpabestie --dbname bumpabestie \
      --tuples-only --no-align --command 'SELECT 1' >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  docker logs "$name" >&2
  echo "PostgreSQL did not become ready: $name" >&2
  return 1
}

start_postgres "$primary" "$primary_data" "$legacy_postgres_image"
docker exec "$primary" psql --username bumpabestie --dbname bumpabestie \
  --set ON_ERROR_STOP=1 \
  --command 'CREATE TABLE infra_restore_probe (id integer PRIMARY KEY, value text NOT NULL);' \
  --command "INSERT INTO infra_restore_probe VALUES (1, 'preserved');" \
  --command "CREATE ROLE infra_preserved_role LOGIN PASSWORD 'infra-role-password' NOSUPERUSER NOBYPASSRLS;" >/dev/null

docker stop --time 30 "$primary" >/dev/null
docker rm "$primary" >/dev/null
start_postgres "$primary" "$primary_data" "$postgres_image"
test "$(
  docker exec "$primary" psql --username bumpabestie --dbname bumpabestie \
    --tuples-only --no-align --command 'SELECT value FROM infra_restore_probe WHERE id = 1'
)" = preserved
test "$(
  docker exec "$primary" psql --username bumpabestie --dbname bumpabestie \
    --tuples-only --no-align \
    --command "SELECT count(*) FROM pg_roles WHERE rolname = 'infra_preserved_role' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls"
)" = 1
docker exec "$primary" postgres --version | grep -F 'PostgreSQL) 16.14' >/dev/null

# Reproduce the ownership created by the former backup image entrypoint, then
# exercise the production one-shot migration before invoking the backup with
# its exact, narrower capability set.
docker run --rm \
  --network none \
  --volume "$backup_data:/backups" \
  --entrypoint sh \
  "$backup_image" \
  -eu -c 'chown -R 70:70 /backups && chmod 0700 /backups'
docker run --rm \
  --network none \
  --user 0:0 \
  --read-only \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add DAC_READ_SEARCH \
  --cap-add FOWNER \
  --security-opt no-new-privileges:true \
  --env BACKUP_DIR=/backups \
  --volume "$backup_data:/backups" \
  --volume "$ROOT_DIR/scripts/init_backup_volume.sh:/usr/local/bin/init-backup-volume:ro" \
  --entrypoint /usr/local/bin/init-backup-volume \
  "$backup_image"
docker run --rm \
  --network "$network" \
  --user 0:0 \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec \
  --cap-drop ALL \
  --cap-add DAC_READ_SEARCH \
  --security-opt no-new-privileges:true \
  --env PGHOST="$primary" \
  --env PGPORT=5432 \
  --env PGUSER=bumpabestie \
  --env PGPASSWORD="$database_password" \
  --env PGDATABASE=bumpabestie \
  --env BACKUP_DIR=/backups \
  --env BACKUP_RETENTION_DAYS=14 \
  --env APPLICATION_REVISION=infra-test \
  --env BACKUP_IMAGE_TAG=infra-test \
  --env BACKUP_IMAGE_REF=bumpabestie-backup@sha256:infra-test \
  --volume "$backup_data:/backups" \
  --volume "$exports_data:/source/exports:ro" \
  --volume "$hermes_data:/source/hermes-runtime:ro" \
  --volume "$hermes_staging:/source/hermes-staging:ro" \
  --entrypoint /usr/local/bin/backup.sh \
  "$backup_image"

backup_path="$(
  docker run --rm --volume "$backup_data:/backups:ro" --entrypoint sh "$backup_image" \
    -c 'find /backups -mindepth 1 -maxdepth 1 -type d | sort | tail -1'
)"
test -n "$backup_path"
docker run --rm --volume "$backup_data:/backups:ro" --entrypoint sh "$backup_image" -eu -c \
  "test -f '$backup_path/hermes-runtime.tar.gz' && test -f '$backup_path/hermes-staging.tar.gz'"
manifest_json="$(
  docker run --rm --volume "$backup_data:/backups:ro" --entrypoint sh "$backup_image" -c \
    "cd '$backup_path' && sha256sum --check SHA256SUMS >/dev/null && cat manifest.json"
)"
jq --exit-status \
  '.format == 3 and .postgres.server_version_num == "160014" and
   .application_revision == "infra-test" and .backup_image_tag == "infra-test" and
   .backup_image_ref == "bumpabestie-backup@sha256:infra-test" and
   (.includes | index("hermes_runtime") != null) and
   (.includes | index("hermes_staging") != null)' \
  <<<"$manifest_json" >/dev/null

docker run --rm \
  --volume "$exports_data:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'rm -rf /artifacts/* && mkdir /artifacts/stale && printf stale > /artifacts/stale/value && chown -R 1000:1000 /artifacts && chmod 0700 /artifacts /artifacts/stale'
docker run --rm \
  --volume "$hermes_data:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'rm -rf /artifacts/* && mkdir /artifacts/stale && printf stale > /artifacts/stale/value && chown -R 1000:1000 /artifacts && chmod 0700 /artifacts /artifacts/stale'
docker run --rm \
  --volume "$hermes_staging:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'rm -rf /artifacts/* && mkdir /artifacts/stale && printf stale > /artifacts/stale/value && chown -R 1000:1000 /artifacts && chmod 0700 /artifacts /artifacts/stale'

start_postgres "$restore" "$restore_data" "$postgres_image"
docker exec "$restore" psql --username bumpabestie --dbname bumpabestie \
  --set ON_ERROR_STOP=1 \
  --command 'CREATE TABLE newer_only_table (id integer PRIMARY KEY);' >/dev/null
# Mirror the production restore service exactly: bypass the legacy backup image
# entrypoint, remain root for the destructive restore, and keep backups read-only.
docker run --rm \
  --network "$network" \
  --user 0:0 \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add DAC_OVERRIDE \
  --cap-add FOWNER \
  --cap-add SETGID \
  --cap-add SETUID \
  --security-opt no-new-privileges:true \
  --env PGHOST="$restore" \
  --env PGPORT=5432 \
  --env PGUSER=bumpabestie \
  --env PGPASSWORD="$database_password" \
  --env PGDATABASE=bumpabestie \
  --env POSTGRES_USER=bumpabestie \
  --env POSTGRES_DB=bumpabestie \
  --env APP_POSTGRES_PASSWORD=infra-app-postgres-password \
  --env BACKUP_DIR=/backups \
  --env RESTORE_CONFIRM=restore-bumpabestie \
  --env BACKUP_PATH="$backup_path" \
  --volume "$backup_data:/backups:ro" \
  --volume "$exports_data:/source/exports" \
  --volume "$hermes_data:/source/hermes-runtime" \
  --volume "$hermes_staging:/source/hermes-staging" \
  --entrypoint /usr/local/bin/restore.sh \
  "$backup_image" >/dev/null

test "$(
  docker exec "$restore" psql --username bumpabestie --dbname bumpabestie \
    --tuples-only --no-align --command 'SELECT value FROM infra_restore_probe WHERE id = 1'
)" = preserved
test "$(
  docker exec "$restore" psql --username bumpabestie --dbname bumpabestie \
    --tuples-only --no-align \
    --command "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = 'newer_only_table'"
)" = 0
test "$(
  docker exec "$restore" psql --username bumpabestie --dbname bumpabestie \
    --tuples-only --no-align \
    --command "SELECT count(*) FROM pg_roles WHERE rolname = 'bumpabestie_app' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls"
)" = 1
test "$(
  docker run --rm \
    --network "$network" \
    --env PGPASSWORD=infra-app-postgres-password \
    --entrypoint psql "$postgres_image" \
    --host "$restore" --username bumpabestie_app --dbname bumpabestie \
    --tuples-only --no-align --command 'SELECT value FROM infra_restore_probe WHERE id = 1'
)" = preserved
docker run --rm \
  --network "$network" \
  --env PGPASSWORD=infra-app-postgres-password \
  --entrypoint psql "$postgres_image" \
  --host "$restore" --username bumpabestie_app --dbname bumpabestie \
  --set ON_ERROR_STOP=1 \
  --command "INSERT INTO infra_restore_probe VALUES (2, 'app-write');" \
  --command "UPDATE infra_restore_probe SET value = 'app-updated' WHERE id = 2;" \
  --command 'DELETE FROM infra_restore_probe WHERE id = 2;' >/dev/null
docker exec \
  --env POSTGRES_USER=bumpabestie \
  --env POSTGRES_DB=bumpabestie \
  --env APP_POSTGRES_PASSWORD=infra-app-rotated-password \
  "$restore" /docker-entrypoint-initdb.d/10-app-role.sh >/dev/null
if docker run --rm \
  --network "$network" \
  --env PGPASSWORD=infra-app-postgres-password \
  --entrypoint psql "$postgres_image" \
  --host "$restore" --username bumpabestie_app --dbname bumpabestie \
  --command 'SELECT 1' >/dev/null 2>&1; then
  echo "The previous application-role password remained valid after rotation" >&2
  exit 1
fi
docker run --rm \
  --network "$network" \
  --env PGPASSWORD=infra-app-rotated-password \
  --entrypoint psql "$postgres_image" \
  --host "$restore" --username bumpabestie_app --dbname bumpabestie \
  --tuples-only --no-align --command 'SELECT 1' | grep -Fx 1 >/dev/null
docker run --rm --volume "$exports_data:/artifacts:ro" --entrypoint sh "$backup_image" \
  -c 'test "$(cat /artifacts/reports/expected.txt)" = expected-export && test ! -e /artifacts/stale'
docker run --rm --volume "$hermes_data:/artifacts:ro" --entrypoint sh "$backup_image" \
  -c 'test "$(cat /artifacts/profile/expected.txt)" = expected-hermes && test ! -e /artifacts/stale'
docker run --rm --volume "$hermes_staging:/artifacts:ro" --entrypoint sh "$backup_image" \
  -c 'test "$(cat /artifacts/control-plane/expected.txt)" = expected-hermes-staging && test ! -e /artifacts/stale'

docker run --rm --entrypoint gosu "$postgres_image" --version | grep -F 'go1.26.5' >/dev/null
docker run --rm --entrypoint postgres "$postgres_image" --version | grep -F 'PostgreSQL) 16.14' >/dev/null
docker run --rm --entrypoint caddy "$caddy_image" version | grep -Fx 'v2.11.4' >/dev/null
docker run --rm --entrypoint caddy "$caddy_image" build-info | grep -F 'go1.26.5' >/dev/null
adapted_caddy_config="$(
  docker run --rm \
    --network none \
    --env CADDY_SITE_SCHEME=http \
    --env APP_DOMAIN=bumpabestie.localhost \
    --env WWW_DOMAIN=www.bumpabestie.localhost \
    --env ADMIN_DOMAIN=admin.bumpabestie.localhost \
    --env RESEARCH_DOMAIN=research.bumpabestie.localhost \
    --env API_DOMAIN=api.bumpabestie.localhost \
    --entrypoint caddy \
    "$caddy_image" adapt --config /etc/caddy/Caddyfile --adapter caddyfile
)"
jq --exit-status '
  .logging.logs.default.encoder == {
    "format": "filter",
    "fields": {
      "request>uri": {
        "filter": "query",
        "actions": [{
          "type": "replace",
          "parameter": "hub.verify_token",
          "value": "REDACTED"
        }]
      }
    },
    "wrap": {"format": "json"}
  } and
  .logging.logs.default.exclude == ["http.log.access"]
' <<<"$adapted_caddy_config" >/dev/null
docker run --rm \
  --env CADDY_SITE_SCHEME=http \
  --env APP_DOMAIN=bumpabestie.localhost \
  --env WWW_DOMAIN=www.bumpabestie.localhost \
  --env ADMIN_DOMAIN=admin.bumpabestie.localhost \
  --env RESEARCH_DOMAIN=research.bumpabestie.localhost \
  --env API_DOMAIN=api.bumpabestie.localhost \
  --entrypoint caddy \
  "$caddy_image" validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
docker run --detach \
  --name "$edge" \
  --network "$network" \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --security-opt no-new-privileges:true \
  --env CADDY_SITE_SCHEME=http \
  --env APP_DOMAIN=bumpabestie.localhost \
  --env WWW_DOMAIN=www.bumpabestie.localhost \
  --env ADMIN_DOMAIN=admin.bumpabestie.localhost \
  --env RESEARCH_DOMAIN=research.bumpabestie.localhost \
  --env API_DOMAIN=api.bumpabestie.localhost \
  --publish 127.0.0.1::80 \
  --volume "$caddy_data:/data" \
  --volume "$caddy_config:/config" \
  "$caddy_image" >/dev/null
test "$(docker exec "$edge" id -u)" = 10001
edge_port="$(docker port "$edge" 80/tcp | sed -E 's/^.*:([0-9]+)$/\1/' | head -1)"
edge_status=""
verify_token_canary="caddy-runtime-secret-canary-$suffix"
for _attempt in {1..30}; do
  edge_status="$(
    curl --silent --output /dev/null --write-out '%{http_code}' \
      --header 'Host: api.bumpabestie.localhost' \
      --get \
      --data-urlencode 'hub.mode=subscribe' \
      --data-urlencode "hub.verify_token=$verify_token_canary" \
      --data-urlencode 'visible_marker=preserved' \
      "http://127.0.0.1:$edge_port/v1/webhooks/whatsapp" || true
  )"
  if [[ "$edge_status" == "503" ]]; then
    break
  fi
  sleep 1
done
test "$edge_status" = 503
caddy_runtime_logs="$(docker logs "$edge" 2>&1)"
if grep -Fq "$verify_token_canary" <<<"$caddy_runtime_logs"; then
  echo "Caddy leaked the webhook verification token in runtime logs" >&2
  exit 1
fi
while IFS= read -r log_line; do
  jq --exit-status . <<<"$log_line" >/dev/null
done <<<"$caddy_runtime_logs"
jq --exit-status --slurp '
  any(
    .[];
    .logger == "http.log.error" and
    .request.host == "api.bumpabestie.localhost" and
    .request.method == "GET" and
    (.request.uri | startswith("/v1/webhooks/whatsapp?")) and
    (.request.uri | contains("hub.verify_token=REDACTED")) and
    (.request.uri | contains("hub.mode=subscribe")) and
    (.request.uri | contains("visible_marker=preserved")) and
    (.status == 502 or .status == 503) and
    (.duration | type == "number") and
    (.msg | type == "string") and
    (.err_id | type == "string") and
    (.err_trace | type == "string")
  )
' <<<"$caddy_runtime_logs" >/dev/null
docker exec "$edge" test -d /data/caddy

echo "Infrastructure image runtime, persistence, backup and isolated restore contracts passed"
