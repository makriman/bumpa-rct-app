#!/usr/bin/env bash
set -Eeuo pipefail
# Never trace inherited credentials, even when invoked from a traced shell.
set +x

if (($# != 4)) || [[ "$1" != pre-migration && "$1" != post-migration ]]; then
  echo "Usage: $0 pre-migration|post-migration POSTGRES_CONTAINER MIGRATION_OWNER DATABASE" >&2
  exit 2
fi
phase="$1"
postgres_container="$2"
migration_owner="$3"
database="$4"
if [[ ! "$postgres_container" =~ ^[a-f0-9]{12,64}$ \
  || -z "$migration_owner" || -z "$database" \
  || -z "${APP_POSTGRES_PASSWORD:-}" || -z "${DATABASE_URL:-}" \
  || -z "${MIGRATION_DATABASE_URL:-}" ]]; then
  echo "Existing application role verification requires the recorded database identity and configured credentials" >&2
  exit 2
fi

# Check the configured application and migration URLs without printing either
# URL or placing credentials in process arguments. The owner checked below must
# be the actual role that creates new tables during this release's migrations.
if ! python3 - "$migration_owner" "$database" <<'PY'
import os
import sys
from urllib.parse import unquote, urlsplit

owner, database = sys.argv[1:]
try:
    for key, user in (("DATABASE_URL", "bumpabestie_app"), ("MIGRATION_DATABASE_URL", owner)):
        url = urlsplit(os.environ[key])
        if not (
            url.scheme in {"postgresql", "postgresql+psycopg"}
            and url.hostname == "postgres"
            and url.port in {None, 5432}
            and unquote(url.username or "") == user
            and unquote(url.path) == "/" + database
            and not url.query and not url.fragment
        ):
            raise ValueError("incompatible database identity")
        if key == "DATABASE_URL" and unquote(url.password or "") != os.environ["APP_POSTGRES_PASSWORD"]:
            raise ValueError("incompatible app credential")
except (KeyError, ValueError):
    raise SystemExit(1)
PY
then
  echo "Configured database identities or app credentials do not match the existing-role release boundary; separate security approval is required to change them" >&2
  exit 2
fi

# Use this exact container's own network address. A loopback connection can hit
# a local trust rule and would not prove that the existing password works.
addresses="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' "$postgres_container")" || exit 2
postgres_address=""
while IFS= read -r address; do
  [[ -z "$address" ]] && continue
  if [[ ! "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "Existing PostgreSQL container has an unverified network address" >&2
    exit 2
  fi
  if [[ -z "$postgres_address" ]]; then
    postgres_address="$address"
  fi
done <<<"$addresses"
if [[ -z "$postgres_address" ]]; then
  echo "Existing PostgreSQL container has no address for a fresh authenticated connection" >&2
  exit 2
fi

# PostgreSQL 16 SYSTEM_USER proves password authentication actually occurred;
# trust, peer, inherited sessions, and SET ROLE cannot satisfy this guard.
# All catalog checks run as the existing non-bypass app role in READ ONLY mode.
# Passing PGPASSWORD by name keeps its value out of Docker command arguments.
if ! checks="$(PGPASSWORD="$APP_POSTGRES_PASSWORD" docker exec --interactive \
  --env PGPASSWORD --env PGPASSFILE=/dev/null --env PGCONNECT_TIMEOUT=5 \
  --env PGSSLMODE=disable --env PGOPTIONS= \
  --env PGSERVICEFILE=/dev/null --env "PGHOSTADDR=$postgres_address" \
  "$postgres_container" env -u PGSERVICE psql --no-psqlrc --no-password --quiet \
  --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --set "migration_owner=$migration_owner" --set "phase=$phase" \
  --host "$postgres_address" --port 5432 --username bumpabestie_app \
  --dbname "$database" 2>/dev/null <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '3s';
SET LOCAL search_path = pg_catalog;
SELECT CASE WHEN current_setting('transaction_read_only') = 'on'
  AND current_user = 'bumpabestie_app' AND session_user = 'bumpabestie_app'
  AND system_user IN ('scram-sha-256:bumpabestie_app', 'md5:bumpabestie_app', 'password:bumpabestie_app')
  THEN 'OK authentication' ELSE 'FAIL authentication' END;
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = current_user
    AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
    AND NOT rolinherit AND NOT rolbypassrls AND NOT rolreplication
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid)
) AND coalesce(current_setting('app.is_privileged', true), '') IN ('', 'false')
  AND coalesce(current_setting('app.current_tenant_id', true), '') = ''
  THEN 'OK role_flags' ELSE 'FAIL role_flags' END;
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
  WHERE d.datname = current_database() AND r.rolname = :'migration_owner'
    AND r.rolname <> current_user
) AND has_database_privilege(current_user, current_database(), 'CONNECT')
  AND has_schema_privilege(current_user, 'public', 'USAGE')
  AND EXISTS (
    SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner
    WHERE n.nspname = 'public' AND r.rolname IN (:'migration_owner', 'pg_database_owner')
  ) THEN 'OK database_schema' ELSE 'FAIL database_schema' END;
SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS required(privilege)
  WHERE NOT EXISTS (
    SELECT 1 FROM pg_default_acl d
      JOIN pg_roles owner_role ON owner_role.oid = d.defaclrole
      CROSS JOIN LATERAL aclexplode(d.defaclacl) AS acl
      JOIN pg_roles app_role ON app_role.oid = acl.grantee
    WHERE owner_role.rolname = :'migration_owner' AND app_role.rolname = current_user
      AND d.defaclobjtype = 'r'
      AND (d.defaclnamespace = 0 OR d.defaclnamespace = 'public'::regnamespace)
      AND acl.privilege_type = required.privilege
  )
) THEN 'OK default_table_acl' ELSE 'FAIL default_table_acl' END;
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
) AND NOT EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_roles owner_role ON owner_role.oid = c.relowner
    CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS required(privilege)
  WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
    AND (owner_role.rolname <> :'migration_owner'
      OR NOT has_table_privilege(current_user, c.oid, required.privilege))
) THEN 'OK current_table_acl' ELSE 'FAIL current_table_acl' END;
SELECT CASE WHEN :'phase' = 'pre-migration' OR NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY[
    'agent_execution_runs','ingress_streams','scoped_agent_memories','proactive_subscriptions'
  ]) AS required(table_name)
  WHERE NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = required.table_name AND c.relkind IN ('r','p')
  )
) THEN 'OK canonical_tables' ELSE 'FAIL canonical_tables' END;
SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
  WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
    AND a.attname = 'tenant_id' AND NOT a.attisdropped
    AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
) THEN 'OK tenant_rls' ELSE 'FAIL tenant_rls' END;
ROLLBACK;
SQL
)"; then
  echo "Existing app credential or read-only catalog verification failed; no roles, credentials or grants were changed. Separate security approval is required for incompatible database access." >&2
  exit 2
fi

expected_checks=$'OK authentication\nOK role_flags\nOK database_schema\nOK default_table_acl\nOK current_table_acl\nOK canonical_tables\nOK tenant_rls'
if [[ "$checks" != "$expected_checks" ]]; then
  while IFS= read -r check; do
    case "$check" in
      'FAIL authentication' | 'FAIL role_flags' | 'FAIL database_schema' | \
      'FAIL default_table_acl' | 'FAIL current_table_acl' | 'FAIL canonical_tables' | 'FAIL tenant_rls')
        echo "Existing app role compatibility rejected: $check; no security settings were changed" >&2 ;;
    esac
  done <<<"$checks"
  echo "Existing app role compatibility is unverified; separate security approval is required for credential, role or grant changes" >&2
  exit 2
fi
echo "Existing application role compatibility passed ($phase; read-only)"
