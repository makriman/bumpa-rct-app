"""Read-only release-role guard contracts; optional Docker fixtures are synthetic."""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "verify_existing_app_role.sh"
CHECKS = ("authentication", "role_flags", "database_schema", "default_table_acl",
          "current_table_acl", "canonical_tables", "tenant_rls")
CONTAINER = "a" * 64
PASSWORD = "synthetic-existing-app-credential"

MOCK_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["ROLE_FIXTURE"])
with (root / "calls").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1] == "inspect":
    print(os.environ.get("ROLE_ADDRESS", "172.17.0.2"))
else:
    (root / "sql").write_text(sys.stdin.read())
    assert os.environ["PGPASSWORD"] == os.environ["APP_POSTGRES_PASSWORD"]
    if os.environ.get("ROLE_FAILURE") == "credentials":
        print("synthetic server error " + os.environ["PGPASSWORD"], file=sys.stderr)
        sys.exit(1)
    for name in json.loads(os.environ["ROLE_CHECKS"]):
        print(("FAIL " if name == os.environ.get("ROLE_FAILURE") else "OK ") + name)
    if os.environ.get("ROLE_FAILURE") == "unexpected":
        print(os.environ["PGPASSWORD"])
'''


class ExistingAppRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        docker = self.root / "docker"
        docker.write_text(MOCK_DOCKER)
        docker.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, *, phase: str = "pre-migration", failure: str = "",
               trace: bool = False, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, ROLE_FIXTURE=str(self.root), ROLE_CHECKS=json.dumps(CHECKS),
                   ROLE_FAILURE=failure, APP_POSTGRES_PASSWORD=PASSWORD,
                   DATABASE_URL=f"postgresql+psycopg://bumpabestie_app:{PASSWORD}@postgres:5432/fixture",
                   MIGRATION_DATABASE_URL="postgresql+psycopg://fixture_owner:synthetic-admin@postgres:5432/fixture")
        env["PATH"] = str(self.root) + os.pathsep + os.environ["PATH"]
        env.update(overrides)
        argv = ["bash", *( ["-x"] if trace else []), str(HELPER), phase, CONTAINER,
                "fixture_owner", "fixture"]
        result = subprocess.run(argv, cwd=self.root, env=env, capture_output=True,
                                text=True, check=False)
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        return result

    def test_valid_existing_role_is_checked_with_read_only_sql_and_no_secret_arguments(self) -> None:
        result = self.invoke(trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        sql = (self.root / "sql").read_text()
        self.assertIn("BEGIN READ ONLY;", sql)
        self.assertIn("ROLLBACK;", sql)
        self.assertIn("system_user IN", sql)
        self.assertNotRegex(sql, r"(?im)^\s*(ALTER|CREATE|GRANT|REVOKE|DROP|INSERT|UPDATE|DELETE|TRUNCATE|CALL)\b")
        calls = (self.root / "calls").read_text()
        self.assertNotIn(PASSWORD, calls)
        self.assertIn('"PGPASSWORD"', calls)
        self.assertIn('"--no-password"', calls)
        self.assertIn('"172.17.0.2"', calls)

    def test_each_incompatible_catalog_result_fails_closed(self) -> None:
        for name in CHECKS:
            with self.subTest(check=name):
                result = self.invoke(failure=name)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("FAIL " + name, result.stderr)
                self.assertIn("separate security approval", result.stderr)

    def test_wrong_credentials_and_unexpected_output_fail_without_secret_leakage(self) -> None:
        for name in ("credentials", "unexpected"):
            with self.subTest(failure=name):
                result = self.invoke(failure=name)
                self.assertEqual(result.returncode, 2)
                self.assertIn("security approval", result.stderr)

    def test_configured_url_credentials_and_migration_owner_must_match(self) -> None:
        for key, value in (
            ("APP_POSTGRES_PASSWORD", "different-synthetic-password"),
            ("DATABASE_URL", f"postgresql://bumpabestie_app:{PASSWORD}@remote.example/fixture"),
            ("MIGRATION_DATABASE_URL", "postgresql://other_owner:synthetic-admin@postgres/fixture"),
        ):
            with self.subTest(key=key):
                result = self.invoke(**{key: value})
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.root / "calls").exists())

    def test_unverified_container_address_and_phase_are_rejected(self) -> None:
        for overrides in ({"ROLE_ADDRESS": ""}, {"ROLE_ADDRESS": "remote.example"},
                          {"phase": "grant-if-needed"}):
            with self.subTest(overrides=overrides):
                result = self.invoke(**overrides)
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.root / "sql").exists())

    def test_postmigration_requires_canonical_tables_and_all_four_privileges(self) -> None:
        result = self.invoke(phase="post-migration")
        self.assertEqual(result.returncode, 0, result.stderr)
        sql = (self.root / "sql").read_text()
        for table in ("agent_execution_runs", "ingress_streams", "scoped_agent_memories",
                      "proactive_subscriptions"):
            self.assertIn("'" + table + "'", sql)
        self.assertIn("ARRAY['SELECT','INSERT','UPDATE','DELETE']", sql)
        self.assertIn("owner_role.rolname = :'migration_owner'", sql)
        self.assertIn("owner_role.rolname <> :'migration_owner'", sql)

    def test_release_checks_role_before_writer_stop_and_on_both_sides_of_migration(self) -> None:
        source = (ROOT / "scripts" / "deploy.sh").read_text()
        prechecks = [match.start() for match in re.finditer(
            r"^verify_app_role_compatibility pre-migration$", source, re.M)]
        self.assertEqual(len(prechecks), 2)
        self.assertLess(prechecks[0], source.index("writer_stop_attempted=1"))
        migration = source.index('"${compose[@]}" --profile tools run --rm migrate')
        self.assertLess(prechecks[1], migration)
        self.assertLess(migration, source.index("\nverify_app_role_compatibility post-migration"))
        self.assertLess(source.index("\nverify_app_role_compatibility post-migration"),
                        source.index("\n  hermes-profile-reconcile"))
        self.assertNotIn("/docker-entrypoint-initdb.d/10-app-role.sh", source)


@unittest.skipUnless(os.environ.get("BUMPABESTIE_LOCAL_ROLE_GUARD_DOCKER") == "1",
                     "explicit disposable local Docker fixture required")
class ExistingAppRolePostgresTests(unittest.TestCase):
    def test_actual_postgres16_credentials_acls_and_schema_without_guard_mutations(self) -> None:
        docker = ["docker", "--context", "desktop-linux"]
        name = "bumpa-existing-role-guard-" + secrets.token_hex(6)
        admin_password = secrets.token_hex(24)
        container = ""

        def command(args: list[str], *, data: str | None = None,
                    env: dict[str, str] | None = None) -> str:
            result = subprocess.run([*docker, *args], input=data, env=env, text=True,
                                    capture_output=True, check=False)
            error = result.stderr.replace(admin_password, "[redacted]").replace(PASSWORD, "[redacted]")
            self.assertEqual(result.returncode, 0, "Local synthetic PostgreSQL fixture command failed: " + error)
            return result.stdout.strip()

        def sql(statement: str) -> str:
            return command(["exec", "--interactive", container, "psql", "-XAtq",
                            "--set", "ON_ERROR_STOP=1", "-U", "fixture_owner", "-d", "fixture"],
                           data=statement)

        def guard(phase: str, password: str = PASSWORD) -> subprocess.CompletedProcess[str]:
            env = dict(os.environ, DOCKER_CONTEXT="desktop-linux", APP_POSTGRES_PASSWORD=password,
                       DATABASE_URL=f"postgresql+psycopg://bumpabestie_app:{password}@postgres:5432/fixture",
                       MIGRATION_DATABASE_URL=f"postgresql+psycopg://fixture_owner:{admin_password}@postgres:5432/fixture")
            result = subprocess.run([str(HELPER), phase, container, "fixture_owner", "fixture"],
                                    env=env, capture_output=True, text=True, check=False)
            self.assertNotIn(password, result.stdout + result.stderr)
            return result

        try:
            container = command(["run", "--detach", "--name", name,
                "--label", "bumpa.test=existing-app-role-guard", "--network", "bridge",
                "--read-only", "--memory=256m", "--pids-limit=128",
                "--tmpfs", "/var/lib/postgresql/data:rw,nosuid,size=128m",
                "--tmpfs", "/var/run/postgresql:rw,nosuid,size=16m", "--tmpfs", "/tmp:rw,nosuid,size=16m",
                "--env", "POSTGRES_USER=fixture_owner", "--env", "POSTGRES_DB=fixture",
                "--env", "POSTGRES_PASSWORD", "postgres:16-alpine@sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36"],
                env=dict(os.environ, POSTGRES_PASSWORD=admin_password))
            self.assertRegex(container, r"^[a-f0-9]{64}$")
            for _ in range(100):
                ready = subprocess.run([*docker, "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "fixture_owner", "-d", "fixture"],
                                       capture_output=True, check=False)
                if ready.returncode == 0:
                    break
                time.sleep(0.1)
            else:
                self.fail("Disposable PostgreSQL fixture did not become ready")
            sql(f"CREATE ROLE bumpabestie_app LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;\n"
                "GRANT CONNECT ON DATABASE fixture TO bumpabestie_app;\n"
                "GRANT USAGE ON SCHEMA public TO bumpabestie_app;\n"
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bumpabestie_app;\n"
                "CREATE TABLE conversations (id text PRIMARY KEY);\n")
            fingerprint = "SELECT rolpassword FROM pg_authid WHERE rolname='bumpabestie_app'; SELECT defaclacl::text FROM pg_default_acl ORDER BY oid; SELECT relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY oid;"
            before = sql(fingerprint)
            first_guard = guard("pre-migration")
            if first_guard.returncode != 0:
                # Diagnose SQL/transport failures only in this synthetic local
                # fixture; the production helper intentionally redacts them.
                address = command(["inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container])
                readonly_sql = HELPER.read_text().split("<<'SQL'\n", 1)[1].split("\nSQL\n", 1)[0]
                command(["exec", "--interactive", "--env", "PGPASSWORD", "--env", "PGPASSFILE=/dev/null",
                    "--env", "PGCONNECT_TIMEOUT=5", "--env", "PGSSLMODE=disable", "--env", "PGOPTIONS=",
                    "--env", "PGSERVICEFILE=/dev/null", "--env", "PGHOSTADDR=" + address,
                    container, "env", "-u", "PGSERVICE", "psql", "-XAtq", "--no-password", "--set", "ON_ERROR_STOP=1",
                    "--set", "migration_owner=fixture_owner", "--set", "phase=pre-migration", "-h", address,
                    "-U", "bumpabestie_app", "-d", "fixture"], data=readonly_sql,
                    env=dict(os.environ, PGPASSWORD=PASSWORD))
            self.assertEqual(first_guard.returncode, 0, first_guard.stderr)
            self.assertEqual(guard("post-migration").returncode, 2)
            self.assertEqual(before, sql(fingerprint))
            sql("CREATE TABLE ingress_streams (scope_key text PRIMARY KEY);\n" + "\n".join(
                f"CREATE TABLE {table} (id text PRIMARY KEY, tenant_id text); ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"
                for table in ("agent_execution_runs", "scoped_agent_memories", "proactive_subscriptions")))
            before = sql(fingerprint)
            post_guard = guard("post-migration")
            self.assertEqual(post_guard.returncode, 0, post_guard.stderr)
            self.assertEqual(guard("pre-migration", "wrong-synthetic-password").returncode, 2)
            self.assertEqual(before, sql(fingerprint))
            sql("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE UPDATE ON TABLES FROM bumpabestie_app;")
            self.assertIn("FAIL default_table_acl", guard("pre-migration").stderr)
            sql("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT UPDATE ON TABLES TO bumpabestie_app; REVOKE INSERT ON ingress_streams FROM bumpabestie_app;")
            self.assertIn("FAIL current_table_acl", guard("post-migration").stderr)
            sql("GRANT INSERT ON ingress_streams TO bumpabestie_app; ALTER ROLE bumpabestie_app BYPASSRLS;")
            self.assertIn("FAIL role_flags", guard("post-migration").stderr)
            sql("ALTER ROLE bumpabestie_app NOBYPASSRLS; ALTER ROLE bumpabestie_app SET app.is_privileged='true';")
            self.assertIn("FAIL role_flags", guard("post-migration").stderr)
        finally:
            if re.fullmatch(r"[a-f0-9]{64}", container):
                command(["rm", "--force", container])


if __name__ == "__main__":
    unittest.main()
