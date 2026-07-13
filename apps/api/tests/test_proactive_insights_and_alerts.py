from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.ids import new_id
from app.db.base import Base
from app.db.models import (
    AsyncJob,
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaSyncRun,
    HermesProfile,
    PhoneIdentity,
    SystemError,
    Tenant,
    TenantMembership,
    User,
    WhatsappMessage,
)
from app.jobs import scheduler
from app.jobs.runtime import PermanentJobError
from app.providers.hermes import HermesReadiness
from app.services.operational_alerts import (
    OperationalAlertClient,
    check_hermes_profile_health,
    deliver_operational_alert,
    discover_operational_alerts,
)
from app.services.proactive_insights import deliver_proactive_insight

SessionLocal = sessionmaker()


@pytest.fixture(autouse=True)
def isolated_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test its own database, including services that commit internally."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setitem(globals(), "SessionLocal", factory)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "whatsapp_backend": "meta",
        "proactive_insights_enabled": True,
        "daily_insights_enabled": True,
        "weekly_insights_enabled": True,
        "meta_phone_number_id": "1234567890",
        "meta_system_user_access_token": "x" * 40,
        "ops_alerts_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def _tenant_owner(db, *, slug: str, timezone: str = "Africa/Lagos"):  # type: ignore[no-untyped-def]
    tenant = Tenant(
        id=new_id(),
        slug=slug,
        name="Private Merchant Name",
        status="active",
        timezone=timezone,
        currency_code="NGN",
        research_consent_status="granted",
    )
    user = User(
        id=new_id(),
        name="Private Owner Name",
        primary_phone_e164=f"+2348{int(tenant.id[:8], 16):010d}"[:14],
        status="active",
    )
    membership = TenantMembership(
        id=new_id(), tenant_id=tenant.id, user_id=user.id, role="owner", status="active"
    )
    identity = PhoneIdentity(
        id=new_id(),
        tenant_id=tenant.id,
        user_id=user.id,
        phone_e164=user.primary_phone_e164,
        status="approved",
        opt_out=False,
    )
    db.add_all((tenant, user, membership, identity))
    db.flush()
    return tenant, user, identity


def _fresh_sync(db, tenant: Tenant, *, finished_at: datetime) -> BumpaSyncRun:  # type: ignore[no-untyped-def]
    connection = BumpaConnection(
        id=new_id(),
        tenant_id=tenant.id,
        encrypted_api_key="encrypted",
        scope_type="business_id",
        scope_id="scope",
        provider="bumpa",
        status="active",
    )
    run = BumpaSyncRun(
        id=new_id(),
        tenant_id=tenant.id,
        bumpa_connection_id=connection.id,
        status="success",
        completion_quality="complete",
        partial_reason=None,
        requested_from=date(2026, 7, 1),
        requested_to=date(2026, 7, 13),
        started_at=finished_at - timedelta(seconds=2),
        finished_at=finished_at,
        error=None,
        orders_availability="available",
        orders_count=7,
        dataset_results={},
    )
    snapshot = BumpaMetricSnapshot(
        id=new_id(),
        tenant_id=tenant.id,
        sync_run_id=run.id,
        metric_key="sales.total_sales",
        metric_title="Total sales",
        value_decimal=Decimal("125000.50"),
        currency_code="NGN",
        requested_from=date(2026, 7, 1),
        requested_to=date(2026, 7, 13),
        availability="available",
    )
    db.add_all((connection, run, snapshot))
    db.flush()
    return run


def test_scheduler_uses_tenant_timezone_and_idempotent_calendar_slots() -> None:
    settings = _settings(
        daily_insight_local_hour=8,
        weekly_insight_local_weekday=0,
        weekly_insight_local_hour=8,
    )
    with SessionLocal() as db:
        tenant, _, _ = _tenant_owner(
            db, slug=f"insight-schedule-{new_id()[:8]}", timezone="Asia/Kolkata"
        )
        now = datetime(2026, 7, 13, 3, 0, tzinfo=UTC)  # Monday 08:30 in India.
        assert scheduler._ensure_proactive_insight_jobs(db, settings=settings, now=now) == 2
        assert scheduler._ensure_proactive_insight_jobs(db, settings=settings, now=now) == 0
        jobs = db.scalars(
            select(AsyncJob)
            .where(AsyncJob.tenant_id == tenant.id, AsyncJob.kind == "whatsapp.proactive_insight")
            .order_by(AsyncJob.id)
        ).all()
        assert {job.payload["cadence"] for job in jobs} == {"daily", "weekly"}
        assert {job.payload["slot"] for job in jobs} == {"2026-07-13", "2026-W29"}
        db.rollback()


def test_insight_delivery_rechecks_consent_opt_out_freshness_and_send_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[dict[str, object]] = []

    class FakeMeta:
        def send_template(self, phone: str, **kwargs: object) -> str:
            delivered.append({"phone": phone, **kwargs})
            return f"wamid-{len(delivered)}"

    monkeypatch.setattr(
        "app.services.proactive_insights.MetaWhatsAppClient.from_settings",
        lambda _settings: FakeMeta(),
    )
    settings = _settings(insight_max_freshness_hours=48)
    now = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    with SessionLocal() as db:
        tenant, _, identity = _tenant_owner(db, slug=f"insight-send-{new_id()[:8]}")
        _fresh_sync(db, tenant, finished_at=now - timedelta(minutes=5))

        first = deliver_proactive_insight(
            db,
            tenant_id=tenant.id,
            cadence="daily",
            slot="2026-07-13",
            settings=settings,
            now=now,
        )
        duplicate = deliver_proactive_insight(
            db,
            tenant_id=tenant.id,
            cadence="daily",
            slot="2026-07-13",
            settings=settings,
            now=now,
        )
        assert first["sent"] == 1 and duplicate["already_sent"] == 1
        assert len(delivered) == 1
        serialized = json.dumps(delivered[0])
        assert tenant.name not in serialized
        assert identity.phone_e164 in str(delivered[0]["phone"])
        assert "NGN 125,000.5" in serialized and "orders: 7" in serialized

        identity.opt_out = True
        db.commit()
        opted_out = deliver_proactive_insight(
            db,
            tenant_id=tenant.id,
            cadence="daily",
            slot="2026-07-14",
            settings=settings,
            now=now,
        )
        assert opted_out["status"] == "no_recipients" and len(delivered) == 1

        identity.opt_out = False
        tenant.research_consent_status = "withdrawn"
        db.commit()
        withdrawn = deliver_proactive_insight(
            db,
            tenant_id=tenant.id,
            cadence="weekly",
            slot="2026-W29",
            settings=settings,
            now=now,
        )
        assert withdrawn["status"] == "ineligible" and len(delivered) == 1
        db.rollback()


def test_stale_bumpa_evidence_never_reaches_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.proactive_insights.MetaWhatsAppClient.from_settings",
        lambda _settings: pytest.fail("stale data reached Meta"),
    )
    now = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    with SessionLocal() as db:
        tenant, _, _ = _tenant_owner(db, slug=f"insight-stale-{new_id()[:8]}")
        _fresh_sync(db, tenant, finished_at=now - timedelta(hours=49))
        result = deliver_proactive_insight(
            db,
            tenant_id=tenant.id,
            cadence="daily",
            slot="2026-07-13",
            settings=_settings(insight_max_freshness_hours=48),
            now=now,
        )
        assert result == {"status": "no_fresh_data", "cadence": "daily", "sent": 0}
        db.rollback()


def test_alert_envelope_is_bounded_hmac_signed_idempotent_and_pii_free(tmp_path: Path) -> None:
    secret = "alert-secret-" + "x" * 40
    secret_path = tmp_path / "alert-secret"
    secret_path.write_text(secret, encoding="utf-8")
    secret_path.chmod(0o600)
    captured: list[httpx.Request] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    settings = _settings(
        proactive_insights_enabled=False,
        daily_insights_enabled=False,
        weekly_insights_enabled=False,
        ops_alerts_enabled=True,
        ops_alert_webhook_url="https://alerts.example.test/v1/events",
        ops_alert_hmac_secret_file=secret_path,
    )
    client = OperationalAlertClient(settings, transport=httpx.MockTransport(receiver))
    payload = {
        "event_type": "whatsapp_delivery_failure",
        "source_type": "whatsapp_message",
        "source_id": "private-message-id",
        "occurred_at": "2026-07-13T09:00:00+00:00",
        "tenant_id": "private-tenant-id",
        "category": "failed",
    }
    first = deliver_operational_alert(payload, settings, client=client)
    second = deliver_operational_alert(payload, settings, client=client)
    assert first == second
    assert len(captured) == 2
    assert captured[0].headers["Idempotency-Key"] == captured[1].headers["Idempotency-Key"]
    body = captured[0].content
    assert len(body) < 16_384
    assert b"private-message-id" not in body and b"private-tenant-id" not in body
    timestamp = captured[0].headers["X-BumpaBestie-Timestamp"]
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    assert captured[0].headers["X-BumpaBestie-Signature"] == f"v1={expected}"


def test_alert_discovery_is_durable_and_idempotent() -> None:
    settings = _settings(
        proactive_insights_enabled=False,
        daily_insights_enabled=False,
        weekly_insights_enabled=False,
        ops_alerts_enabled=True,
        ops_alert_scan_lookback_hours=24,
    )
    with SessionLocal() as db:
        tenant, user, _ = _tenant_owner(db, slug=f"alert-discovery-{new_id()[:8]}")
        occurred_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        failed = WhatsappMessage(
            id=new_id(),
            tenant_id=tenant.id,
            user_id=user.id,
            direction="outbound",
            message_type="template",
            status="failed",
            payload={},
            created_at=occurred_at,
        )
        hermes_error = SystemError(
            id=new_id(),
            tenant_id=tenant.id,
            service="hermes",
            severity="error",
            message="Hermes call failed",
            error_metadata={"category": "hermes_unavailable"},
            created_at=occurred_at,
        )
        db.add_all((failed, hermes_error))
        db.flush()
        now = occurred_at + timedelta(seconds=1)
        assert discover_operational_alerts(db, settings, now=now) == 2
        assert discover_operational_alerts(db, settings, now=now) == 0
        jobs = db.scalars(
            select(AsyncJob).where(
                AsyncJob.tenant_id == tenant.id,
                AsyncJob.kind == "ops.deliver_alert",
            )
        ).all()
        assert {job.payload["event_type"] for job in jobs} == {
            "whatsapp_delivery_failure",
            "hermes_call_failure",
        }
        assert all(user.primary_phone_e164 not in json.dumps(job.payload) for job in jobs)
        db.rollback()


def test_alert_discovery_advances_past_an_idempotent_scan_batch() -> None:
    settings = _settings(
        proactive_insights_enabled=False,
        daily_insights_enabled=False,
        weekly_insights_enabled=False,
        ops_alerts_enabled=True,
        ops_alert_scan_lookback_hours=24,
        ops_alert_scan_limit=2,
    )
    with SessionLocal() as db:
        tenant, user, _ = _tenant_owner(db, slug=f"alert-batch-{new_id()[:8]}")
        occurred_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        rows = [
            WhatsappMessage(
                id=new_id(),
                tenant_id=tenant.id,
                user_id=user.id,
                direction="outbound",
                message_type="template",
                status="failed",
                payload={},
                created_at=occurred_at + timedelta(seconds=index),
            )
            for index in range(3)
        ]
        db.add_all(rows)
        db.flush()
        now = occurred_at + timedelta(minutes=1)

        assert discover_operational_alerts(db, settings, now=now) == 2
        assert discover_operational_alerts(db, settings, now=now) == 1
        assert discover_operational_alerts(db, settings, now=now) == 0
        keys = set(
            db.scalars(
                select(AsyncJob.idempotency_key).where(
                    AsyncJob.kind == "ops.deliver_alert",
                    AsyncJob.tenant_id == tenant.id,
                )
            ).all()
        )
        assert keys == {
            f"ops-alert:whatsapp_delivery_failure:whatsapp_message:{row.id}" for row in rows
        }


def test_hermes_health_failure_records_system_error_and_queues_alert() -> None:
    class UnreadyHermes:
        def readiness(self, _endpoint):  # type: ignore[no-untyped-def]
            return HermesReadiness(ready=False, status="degraded", latency_ms=12)

    settings = _settings(
        proactive_insights_enabled=False,
        daily_insights_enabled=False,
        weekly_insights_enabled=False,
        ops_alerts_enabled=True,
    )
    with SessionLocal() as db:
        tenant, _, _ = _tenant_owner(db, slug=f"hermes-alert-{new_id()[:8]}")
        profile = HermesProfile(
            id=new_id(),
            tenant_id=tenant.id,
            profile_name=f"tenant_{tenant.id[:8]}",
            profile_path="/profiles/private",
            provider="hermes",
            api_internal_url="http://hermes:8700/v1",
            api_port=8700,
            encrypted_api_key=FieldCipher(settings.field_encryption_key).encrypt("profile-key"),
            status="active",
        )
        db.add(profile)
        db.flush()
        result = check_hermes_profile_health(
            db,
            profile_id=profile.id,
            settings=settings,
            client=UnreadyHermes(),  # type: ignore[arg-type]
        )
        assert result == {"status": "alert_queued", "category": "not_ready"}
        assert profile.status == "degraded"
        error = db.scalar(
            select(SystemError).where(
                SystemError.tenant_id == tenant.id,
                SystemError.service == "hermes_health",
            )
        )
        assert error is not None and error.stack is None
        alert = db.scalar(
            select(AsyncJob).where(
                AsyncJob.tenant_id == tenant.id,
                AsyncJob.kind == "ops.deliver_alert",
            )
        )
        assert alert is not None and alert.payload["event_type"] == "hermes_health_failure"
        db.rollback()


def test_non_retryable_alert_destination_failure_is_terminal(tmp_path: Path) -> None:
    secret_path = tmp_path / "secret"
    secret_path.write_text("z" * 40, encoding="utf-8")
    secret_path.chmod(0o600)
    settings = _settings(
        proactive_insights_enabled=False,
        daily_insights_enabled=False,
        weekly_insights_enabled=False,
        ops_alerts_enabled=True,
        ops_alert_webhook_url="https://alerts.example.test/v1/events",
        ops_alert_hmac_secret_file=secret_path,
    )
    client = OperationalAlertClient(
        settings,
        transport=httpx.MockTransport(lambda _request: httpx.Response(400, content=b"private")),
    )
    with pytest.raises(PermanentJobError, match="rejected delivery"):
        deliver_operational_alert(
            {
                "event_type": "bumpa_sync_failure",
                "source_type": "bumpa_sync_run",
                "source_id": "source",
                "occurred_at": "2026-07-13T09:00:00+00:00",
                "tenant_id": None,
                "category": "failed",
            },
            settings,
            client=client,
        )
