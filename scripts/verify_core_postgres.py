"""Disposable local PostgreSQL migration/RLS gate; never accepts a remote URL.

Uses the installed local Docker Desktop context, tmpfs data and a loopback port.
All fixture credentials and rows are synthetic; the owned container is removed.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "postgres:16-alpine@sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36"
DOCKER = ["docker", "--context", "desktop-linux"]


def _run_retention_gate(url: str, environment: dict[str, str]) -> None:
    candidate = urlsplit(url)
    if (
        candidate.scheme != "postgresql+psycopg"
        or candidate.hostname != "127.0.0.1"
        or candidate.path != "/bumpa_core_synthetic"
        or candidate.username != "synthetic_admin"
    ):
        raise RuntimeError(
            "Retention verification only accepts the owned disposable loopback database"
        )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "tests/test_operational_retention.py::test_postgres_overlapping_retention_batches_are_disjoint_and_drain",
        ],
        cwd=ROOT / "apps/api",
        env={**environment, "POSTGRES_RETENTION_TEST_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if (
        result.returncode != 0
        or not re.search(r"\b1 passed\b", result.stdout)
        or re.search(r"\bskipped\b", result.stdout)
    ):
        raise RuntimeError(
            "The disposable PostgreSQL retention concurrency test did not pass"
        )


def main() -> None:
    tag = secrets.token_hex(6)
    password = secrets.token_hex(24)
    name = "bumpa-core-rls-" + tag
    container = ""
    try:
        result = subprocess.run(
            [
                *DOCKER,
                "run",
                "--detach",
                "--pull=missing",
                "--name",
                name,
                "--label",
                "bumpa.test=core-repair-rls",
                "--memory=512m",
                "--cpus=1",
                "--pids-limit=128",
                "--read-only",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,nosuid,size=256m",
                "--tmpfs",
                "/var/run/postgresql:rw,nosuid,size=16m",
                "--tmpfs",
                "/tmp:rw,nosuid,size=16m",
                "--publish",
                "127.0.0.1::5432",
                "--env",
                "POSTGRES_DB=bumpa_core_synthetic",
                "--env",
                "POSTGRES_USER=synthetic_admin",
                "--env",
                "POSTGRES_PASSWORD=" + password,
                IMAGE,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        container = result.stdout.strip()
        if not re.fullmatch("[a-f0-9]{64}", container):
            raise RuntimeError("Invalid disposable container identity")
        for _ in range(150):
            ready = subprocess.run(
                [
                    *DOCKER,
                    "exec",
                    container,
                    "pg_isready",
                    "-U",
                    "synthetic_admin",
                    "-d",
                    "bumpa_core_synthetic",
                ],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Disposable database did not become ready")
        port = (
            subprocess.check_output([*DOCKER, "port", container, "5432/tcp"], text=True)
            .strip()
            .split(":")[-1]
        )
        if not port.isdigit():
            raise RuntimeError("Invalid local database port")
        url = f"postgresql+psycopg://synthetic_admin:{password}@127.0.0.1:{port}/bumpa_core_synthetic"
        env = {
            **os.environ,
            "APP_ENV": "test",
            "DATABASE_URL": url,
            "MIGRATION_DATABASE_URL": url,
            "SEED_DEMO_DATA": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT / "apps/api",
            env=env,
            check=True,
        )
        os.environ.update(
            {
                "APP_ENV": "test",
                "DATABASE_URL": url,
                "MIGRATION_DATABASE_URL": url,
                "SEED_DEMO_DATA": "false",
            }
        )
        sys.path.insert(0, str(ROOT / "apps/api"))
        from scripts.verify_postgres_rls import (
            _assert_schema_and_policies,
            _cleanup,
            _create_role,
            _discover_tenant_tables,
            _exercise_role,
            _fixture_pair,
            _seed,
        )
        from sqlalchemy import create_engine
        from verify_core_postgres_concurrency import verify_core_concurrency

        admin = create_engine(
            url,
            connect_args={
                "options": "-c statement_timeout=10000 -c lock_timeout=10000"
            },
        )
        rows, ids, tenants, users = _fixture_pair(tag)
        role = "core_gate_" + tag
        app = None
        try:
            with admin.connect() as connection:
                tables = _discover_tenant_tables(connection)
                _assert_schema_and_policies(connection, tables)
            _seed(admin, rows)
            _create_role(admin, role, password, tables)
            app = create_engine(admin.url.set(username=role, password=password))
            _exercise_role(app, admin, role, tables, tenants, users)
            concurrency = verify_core_concurrency(admin, tag)
            print(
                json.dumps(
                    {
                        "stage": "postgres_core_concurrency",
                        "passed": True,
                        "concurrency": concurrency,
                    }
                ),
                flush=True,
            )
        finally:
            _cleanup(admin, app, role, ids, tenants, users)
            admin.dispose()
        _run_retention_gate(url, env)
        print(
            json.dumps(
                {
                    "passed": True,
                    "database": "disposable local PostgreSQL16",
                    "tenant_tables": len(tables),
                    "new_execution_table_covered": "agent_execution_runs" in tables,
                    "checks": [
                        "head migration",
                        "forced RLS",
                        "non-bypass role",
                        "zero-context denial",
                        "cross-tenant reads/writes",
                        "OTP/voice policy",
                        "actual concurrent ingress/head locks",
                        "same-worker claim epoch and lost lease",
                        "serialized reconciliation claims",
                        "actual final publication versus recovery",
                        "PostgreSQL overlapping retention test",
                    ],
                    "concurrency": concurrency,
                    "retention_concurrency_test": "passed (not skipped)",
                    "production_access": False,
                }
            )
        )
    finally:
        if container and re.fullmatch("[a-f0-9]{64}", container):
            subprocess.run(
                [*DOCKER, "rm", "--force", container],
                check=True,
                stdout=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    main()
