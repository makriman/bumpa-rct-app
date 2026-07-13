from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, delete, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models import AsyncJob, AuditLog, JobOutbox, SystemError
from app.jobs import handlers as job_handlers
from app.jobs.handlers import operational_retention_handler
from app.jobs.runtime import PermanentJobError
from app.services import operational_retention as operational_retention_service
from app.services.operational_retention import (
    _expired_id_statement,
    cleanup_operational_history,
)


def _audit(*, identifier: str, created_at: datetime) -> AuditLog:
    return AuditLog(
        id=identifier,
        action="retention.fixture",
        created_at=created_at,
    )


def _error(*, identifier: str, created_at: datetime) -> SystemError:
    return SystemError(
        id=identifier,
        service="retention_fixture",
        severity="error",
        message="Sanitized retention fixture",
        error_metadata={},
        created_at=created_at,
    )


def _postgres_retention_engine() -> Engine:
    """Return only an explicitly isolated PostgreSQL test engine.

    CI already provides a migrated disposable PostgreSQL service through
    ``SYNC_DATABASE_URL``. Local runs must opt in with the retention-specific
    variable so an ordinary unit-test command can never clean a developer or
    production database by accident.
    """

    raw_url = os.environ.get("POSTGRES_RETENTION_TEST_DATABASE_URL")
    if not raw_url and os.environ.get("CI", "").lower() == "true":
        raw_url = os.environ.get("SYNC_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "set POSTGRES_RETENTION_TEST_DATABASE_URL to a migrated disposable PostgreSQL database"
        )
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise AssertionError("The operational-retention concurrency gate requires PostgreSQL")
    return create_engine(url.set(drivername="postgresql+psycopg"), pool_pre_ping=True)


def _cleanup_postgres_retention_fixtures(
    engine: Engine,
    *,
    root_job_ids: tuple[str, str],
    audit_ids: tuple[str, ...],
    error_ids: tuple[str, ...],
) -> None:
    """Remove roots and every recursively generated continuation from the gate."""

    job_ids = set(root_job_ids)
    with Session(engine) as db:
        frontier = set(root_job_ids)
        while frontier:
            continuation_keys = {
                f"operational-retention:continuation:{job_id}" for job_id in frontier
            }
            children = set(
                db.scalars(
                    select(AsyncJob.id).where(AsyncJob.idempotency_key.in_(continuation_keys))
                )
            )
            frontier = children - job_ids
            job_ids.update(children)
        if job_ids:
            db.execute(delete(JobOutbox).where(JobOutbox.job_id.in_(job_ids)))
            db.execute(delete(AsyncJob).where(AsyncJob.id.in_(job_ids)))
        db.execute(delete(AuditLog).where(AuditLog.id.in_(audit_ids)))
        db.execute(delete(SystemError).where(SystemError.id.in_(error_ids)))
        db.commit()


def test_cleanup_is_oldest_first_bounded_and_preserves_window_boundaries() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    audit_cutoff = now - timedelta(days=30)
    error_cutoff = now - timedelta(days=7)

    with Session(engine) as db:
        db.add_all(
            [
                _audit(identifier="audit-oldest", created_at=audit_cutoff - timedelta(days=3)),
                _audit(identifier="audit-older", created_at=audit_cutoff - timedelta(days=2)),
                _audit(identifier="audit-old", created_at=audit_cutoff - timedelta(days=1)),
                _audit(identifier="audit-boundary", created_at=audit_cutoff),
                _error(identifier="error-oldest", created_at=error_cutoff - timedelta(days=3)),
                _error(identifier="error-older", created_at=error_cutoff - timedelta(days=2)),
                _error(identifier="error-old", created_at=error_cutoff - timedelta(days=1)),
                _error(identifier="error-boundary", created_at=error_cutoff),
            ]
        )
        db.commit()

        first = cleanup_operational_history(
            db,
            audit_log_retention_days=30,
            system_error_retention_days=7,
            limit=2,
            now=now,
        )
        assert first == {"audit_logs_deleted": 2, "system_errors_deleted": 2}
        assert set(db.scalars(select(AuditLog.id))) == {"audit-old", "audit-boundary"}
        assert set(db.scalars(select(SystemError.id))) == {"error-old", "error-boundary"}

        second = cleanup_operational_history(
            db,
            audit_log_retention_days=30,
            system_error_retention_days=7,
            limit=2,
            now=now,
        )
        assert second == {"audit_logs_deleted": 1, "system_errors_deleted": 1}
        assert set(db.scalars(select(AuditLog.id))) == {"audit-boundary"}
        assert set(db.scalars(select(SystemError.id))) == {"error-boundary"}


@pytest.mark.parametrize("model", [AuditLog, SystemError])
def test_cleanup_candidates_lock_and_skip_rows_owned_by_parallel_workers(
    model: type[AuditLog] | type[SystemError],
) -> None:
    statement = _expired_id_statement(
        model,
        cutoff=datetime(2026, 7, 13, tzinfo=UTC),
        limit=500,
    )
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE SKIP LOCKED" in compiled


def test_postgres_overlapping_retention_batches_are_disjoint_and_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _postgres_retention_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    audit_cutoff = now - timedelta(days=30)
    error_cutoff = now - timedelta(days=7)
    # Keep the boundary fixture IDs within the schema's 36-character UUID-width
    # column while retaining enough entropy for parallel CI processes.
    run_tag = os.urandom(4).hex()
    root_job_ids = (
        f"retention-root-a-{run_tag}",
        f"retention-root-b-{run_tag}",
    )
    audit_expired_ids = tuple(f"retention-audit-{run_tag}-{index}" for index in range(5))
    audit_boundary_id = f"retention-audit-{run_tag}-boundary"
    audit_ids = (*audit_expired_ids, audit_boundary_id)
    error_expired_ids = tuple(f"retention-error-{run_tag}-{index}" for index in range(5))
    error_boundary_id = f"retention-error-{run_tag}-boundary"
    error_ids = (*error_expired_ids, error_boundary_id)
    all_audit_ids = set(audit_ids)
    all_error_ids = set(error_ids)
    settings = SimpleNamespace(
        audit_log_retention_days=30,
        system_error_retention_days=7,
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(operational_retention_service, "utcnow", lambda: now)

    first_db = factory()
    second_done = threading.Event()
    second_lock_attempted = threading.Event()
    outcome_lock = threading.Lock()
    outcomes: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    observed_lock_sql: list[tuple[str, str]] = []
    observed_lock_sql_guard = threading.Lock()
    main_thread_name = threading.current_thread().name

    def fixture_ids(db: Session, model: type[AuditLog] | type[SystemError]) -> set[str]:
        ids = all_audit_ids if model is AuditLog else all_error_ids
        return set(db.scalars(select(model.id).where(model.id.in_(ids))))

    def observe_lock_sql(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.upper().split())
        if "FOR UPDATE SKIP LOCKED" not in normalized:
            return
        table = "audit_logs" if "AUDIT_LOGS" in normalized else "system_errors"
        worker = threading.current_thread().name
        with observed_lock_sql_guard:
            observed_lock_sql.append((worker, table))
        if worker == "retention-worker-b":
            second_lock_attempted.set()

    def second_worker() -> None:
        try:
            with factory() as db:
                root = db.get(AsyncJob, root_job_ids[1])
                if root is None:
                    raise AssertionError("The second retention root disappeared")
                result = operational_retention_handler(db, root)
                remaining = {
                    "audit": fixture_ids(db, AuditLog),
                    "errors": fixture_ids(db, SystemError),
                }
                db.commit()
            with outcome_lock:
                outcomes["second"] = {"result": result, "remaining": remaining}
        except Exception as exc:  # pragma: no cover - asserted below with the concrete failure
            with outcome_lock:
                failures["second"] = f"{type(exc).__name__}: {exc}"
        finally:
            second_done.set()

    second_thread = threading.Thread(
        target=second_worker,
        name="retention-worker-b",
        daemon=True,
    )
    event.listen(engine, "before_cursor_execute", observe_lock_sql)
    try:
        with engine.connect() as connection:
            existing_audits = connection.scalar(
                select(AuditLog.id).where(AuditLog.created_at < audit_cutoff).limit(1)
            )
            existing_errors = connection.scalar(
                select(SystemError.id).where(SystemError.created_at < error_cutoff).limit(1)
            )
        if existing_audits is not None or existing_errors is not None:
            pytest.fail(
                "PostgreSQL retention concurrency gate requires an isolated database with no "
                "pre-existing expired operational rows"
            )

        with factory() as seed_db:
            seed_db.add_all(
                [
                    _audit(
                        identifier=identifier,
                        created_at=audit_cutoff - timedelta(seconds=5 - index),
                    )
                    for index, identifier in enumerate(audit_expired_ids)
                ]
                + [_audit(identifier=audit_boundary_id, created_at=audit_cutoff)]
                + [
                    _error(
                        identifier=identifier,
                        created_at=error_cutoff - timedelta(seconds=5 - index),
                    )
                    for index, identifier in enumerate(error_expired_ids)
                ]
                + [_error(identifier=error_boundary_id, created_at=error_cutoff)]
                + [
                    AsyncJob(
                        id=root_job_id,
                        kind="system.cleanup_operational_history",
                        payload={"limit": 2},
                        idempotency_key=f"operational-retention:root:{root_job_id}",
                        available_at=now,
                    )
                    for root_job_id in root_job_ids
                ]
            )
            seed_db.commit()

        first_root = first_db.get(AsyncJob, root_job_ids[0])
        assert first_root is not None
        first_result = operational_retention_handler(first_db, first_root)
        assert first_result == {
            "audit_logs_deleted": 2,
            "system_errors_deleted": 2,
            "continuation_enqueued": True,
        }
        assert fixture_ids(first_db, AuditLog) == {
            *audit_expired_ids[2:],
            audit_boundary_id,
        }
        assert fixture_ids(first_db, SystemError) == {
            *error_expired_ids[2:],
            error_boundary_id,
        }

        second_thread.start()
        assert second_lock_attempted.wait(timeout=5), (
            "The overlapping PostgreSQL worker never attempted a locked candidate query"
        )
        assert second_done.wait(timeout=5), (
            "The overlapping PostgreSQL worker blocked instead of skipping locked rows"
        )
        assert failures == {}
        assert outcomes["second"]["result"] == {
            "audit_logs_deleted": 2,
            "system_errors_deleted": 2,
            "continuation_enqueued": True,
        }
        # Worker B cannot see worker A's uncommitted deletes, but its own view
        # proves it selected the next two rows rather than either locked row.
        assert outcomes["second"]["remaining"] == {
            "audit": {
                audit_expired_ids[0],
                audit_expired_ids[1],
                audit_expired_ids[4],
                audit_boundary_id,
            },
            "errors": {
                error_expired_ids[0],
                error_expired_ids[1],
                error_expired_ids[4],
                error_boundary_id,
            },
        }
        first_db.commit()
        second_thread.join(timeout=5)
        assert not second_thread.is_alive()
        assert observed_lock_sql == [
            (main_thread_name, "audit_logs"),
            (main_thread_name, "system_errors"),
            ("retention-worker-b", "audit_logs"),
            ("retention-worker-b", "system_errors"),
        ]

        with factory() as db:
            assert fixture_ids(db, AuditLog) == {audit_expired_ids[4], audit_boundary_id}
            assert fixture_ids(db, SystemError) == {error_expired_ids[4], error_boundary_id}
            continuation_jobs = list(
                db.scalars(
                    select(AsyncJob)
                    .where(
                        AsyncJob.idempotency_key.in_(
                            {
                                f"operational-retention:continuation:{root_job_id}"
                                for root_job_id in root_job_ids
                            }
                        )
                    )
                    .order_by(AsyncJob.idempotency_key)
                )
            )
            assert len(continuation_jobs) == 2
            continuation_ids = [job.id for job in continuation_jobs]
            assert set(
                db.scalars(select(JobOutbox.job_id).where(JobOutbox.job_id.in_(continuation_ids)))
            ) == set(continuation_ids)

        continuation_results: list[dict[str, Any] | None] = []
        for continuation_id in continuation_ids:
            with factory() as db:
                continuation = db.get(AsyncJob, continuation_id)
                assert continuation is not None
                continuation_results.append(operational_retention_handler(db, continuation))
                db.commit()
        assert continuation_results == [
            {
                "audit_logs_deleted": 1,
                "system_errors_deleted": 1,
                "continuation_enqueued": False,
            },
            {
                "audit_logs_deleted": 0,
                "system_errors_deleted": 0,
                "continuation_enqueued": False,
            },
        ]
        assert sum(
            int(result["audit_logs_deleted"])
            for result in [first_result, outcomes["second"]["result"], *continuation_results]
            if result is not None
        ) == len(audit_expired_ids)
        assert sum(
            int(result["system_errors_deleted"])
            for result in [first_result, outcomes["second"]["result"], *continuation_results]
            if result is not None
        ) == len(error_expired_ids)

        with factory() as db:
            assert fixture_ids(db, AuditLog) == {audit_boundary_id}
            assert fixture_ids(db, SystemError) == {error_boundary_id}
            assert (
                db.scalar(
                    select(AsyncJob.id).where(
                        AsyncJob.idempotency_key.in_(
                            {
                                f"operational-retention:continuation:{continuation_id}"
                                for continuation_id in continuation_ids
                            }
                        )
                    )
                )
                is None
            )
    finally:
        event.remove(engine, "before_cursor_execute", observe_lock_sql)
        if first_db.in_transaction():
            first_db.rollback()
        first_db.close()
        if second_thread.is_alive():
            second_thread.join(timeout=10)
        _cleanup_postgres_retention_fixtures(
            engine,
            root_job_ids=root_job_ids,
            audit_ids=audit_ids,
            error_ids=error_ids,
        )
        engine.dispose()


def test_durable_handler_uses_configured_windows_and_rejects_unbounded_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    settings = SimpleNamespace(
        audit_log_retention_days=365,
        system_error_retention_days=90,
    )
    monkeypatch.setattr("app.jobs.handlers.get_settings", lambda: settings)

    with Session(engine) as db:
        valid = AsyncJob(kind="system.cleanup_operational_history", payload={"limit": 17})
        assert operational_retention_handler(db, valid) == {
            "audit_logs_deleted": 0,
            "system_errors_deleted": 0,
            "continuation_enqueued": False,
        }

        for invalid in (True, 0, 1001, "100"):
            malformed = AsyncJob(
                kind="system.cleanup_operational_history",
                payload={"limit": invalid},
            )
            with pytest.raises(PermanentJobError, match="limit is invalid"):
                operational_retention_handler(db, malformed)


def test_durable_handler_enqueues_bounded_continuations_until_backlog_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        audit_log_retention_days=30,
        system_error_retention_days=7,
    )
    monkeypatch.setattr("app.jobs.handlers.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.operational_retention.utcnow", lambda: now)

    with Session(engine) as db:
        db.add_all(
            [
                _audit(
                    identifier=f"audit-expired-{index}",
                    created_at=now - timedelta(days=31, seconds=index),
                )
                for index in range(3)
            ]
        )
        root = AsyncJob(
            kind="system.cleanup_operational_history",
            payload={"limit": 2},
            idempotency_key="operational-retention:root",
        )
        db.add(root)
        db.commit()

        first = operational_retention_handler(db, root)
        assert first == {
            "audit_logs_deleted": 2,
            "system_errors_deleted": 0,
            "continuation_enqueued": True,
        }
        continuation = db.scalar(
            select(AsyncJob).where(
                AsyncJob.idempotency_key == f"operational-retention:continuation:{root.id}"
            )
        )
        assert continuation is not None

        second = operational_retention_handler(db, continuation)
        assert second == {
            "audit_logs_deleted": 1,
            "system_errors_deleted": 0,
            "continuation_enqueued": False,
        }
        assert db.scalar(select(AuditLog.id)) is None


def test_retention_configuration_is_bounded_and_covers_alert_discovery() -> None:
    configured = Settings(
        audit_log_retention_days=730,
        system_error_retention_days=120,
        operational_retention_batch_size=250,
        ops_alert_scan_lookback_hours=2160,
    )
    assert configured.audit_log_retention_days == 730
    assert configured.system_error_retention_days == 120
    assert configured.operational_retention_batch_size == 250

    with pytest.raises(ValidationError, match="audit_log_retention_days"):
        Settings(audit_log_retention_days=29)
    with pytest.raises(ValidationError, match="operational_retention_batch_size"):
        Settings(operational_retention_batch_size=1001)
    with pytest.raises(ValidationError, match="must cover OPS_ALERT_SCAN_LOOKBACK_HOURS"):
        Settings(
            system_error_retention_days=7,
            ops_alert_scan_lookback_hours=169,
        )
