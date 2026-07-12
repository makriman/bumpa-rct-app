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
caddy_data="bumpabestie-infra-caddy-data-$suffix"
caddy_config="bumpabestie-infra-caddy-config-$suffix"
primary="bumpabestie-infra-primary-$suffix"
restore="bumpabestie-infra-restore-$suffix"
edge="bumpabestie-infra-edge-$suffix"

postgres_image="bumpabestie-postgres:infra-test"
backup_image="bumpabestie-backup:infra-test"
caddy_image="bumpabestie-caddy:infra-test"
legacy_postgres_image="postgres:16.9-alpine@sha256:7c688148e5e156d0e86df7ba8ae5a05a2386aaec1e2ad8e6d11bdf10504b1fb7"
database_password="infra-test-postgres-only"

cleanup() {
  result=$?
  docker rm -f "$primary" "$restore" "$edge" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker volume rm -f \
    "$primary_data" "$restore_data" "$backup_data" \
    "$exports_data" "$hermes_data" "$caddy_data" "$caddy_config" >/dev/null 2>&1 || true
  exit "$result"
}
trap cleanup EXIT

if [[ "${SKIP_INFRA_BUILD:-false}" != "true" ]]; then
  docker build --target runtime --tag "$postgres_image" --file infra/postgres/Dockerfile .
  docker build --target backup --tag "$backup_image" --file infra/postgres/Dockerfile .
  docker build --tag "$caddy_image" --file infra/caddy/Dockerfile .
fi

docker network create "$network" >/dev/null
for volume in "$primary_data" "$restore_data" "$backup_data" "$exports_data" "$hermes_data" "$caddy_data" "$caddy_config"; do
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

docker run --rm \
  --network "$network" \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add FOWNER \
  --cap-add SETGID \
  --cap-add SETUID \
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
  --volume "$exports_data:/source/exports" \
  --volume "$hermes_data:/source/hermes" \
  "$backup_image"

backup_path="$(
  docker run --rm --volume "$backup_data:/backups:ro" --entrypoint sh "$backup_image" \
    -c 'find /backups -mindepth 1 -maxdepth 1 -type d | sort | tail -1'
)"
test -n "$backup_path"
manifest_json="$(
  docker run --rm --volume "$backup_data:/backups:ro" --entrypoint sh "$backup_image" -c \
    "cd '$backup_path' && sha256sum --check SHA256SUMS >/dev/null && cat manifest.json"
)"
jq --exit-status \
  '.format == 2 and .postgres.server_version_num == "160014" and
   .application_revision == "infra-test" and .backup_image_tag == "infra-test" and
   .backup_image_ref == "bumpabestie-backup@sha256:infra-test"' \
  <<<"$manifest_json" >/dev/null

docker run --rm \
  --volume "$exports_data:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'rm -rf /artifacts/* && mkdir /artifacts/stale && printf stale > /artifacts/stale/value && chown -R 1000:1000 /artifacts && chmod 0700 /artifacts /artifacts/stale'
docker run --rm \
  --volume "$hermes_data:/artifacts" \
  --entrypoint sh "$backup_image" \
  -c 'rm -rf /artifacts/* && mkdir /artifacts/stale && printf stale > /artifacts/stale/value && chown -R 1000:1000 /artifacts && chmod 0700 /artifacts /artifacts/stale'

start_postgres "$restore" "$restore_data" "$postgres_image"
docker exec "$restore" psql --username bumpabestie --dbname bumpabestie \
  --set ON_ERROR_STOP=1 \
  --command 'CREATE TABLE newer_only_table (id integer PRIMARY KEY);' >/dev/null
docker run --rm \
  --network "$network" \
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
  --volume "$hermes_data:/source/hermes" \
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

docker run --rm --entrypoint gosu "$postgres_image" --version | grep -F 'go1.26.5' >/dev/null
docker run --rm --entrypoint postgres "$postgres_image" --version | grep -F 'PostgreSQL) 16.14' >/dev/null
docker run --rm --entrypoint caddy "$caddy_image" version | grep -Fx 'v2.11.4' >/dev/null
docker run --rm --entrypoint caddy "$caddy_image" build-info | grep -F 'go1.26.5' >/dev/null
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
for _attempt in {1..30}; do
  edge_status="$(
    curl --silent --output /dev/null --write-out '%{http_code}' \
      --header 'Host: bumpabestie.localhost' "http://127.0.0.1:$edge_port/" || true
  )"
  if [[ "$edge_status" == "503" ]]; then
    break
  fi
  sleep 1
done
test "$edge_status" = 503
docker exec "$edge" test -d /data/caddy

echo "Infrastructure image runtime, persistence, backup and isolated restore contracts passed"
