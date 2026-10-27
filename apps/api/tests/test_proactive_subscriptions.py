from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.core.dependencies import Principal, require_tenant_admin
from app.db.base import Base
from app.db.models import (
    AgentMessage,
    AsyncJob,
    BumpaConnection,
    BumpaOrder,
    BumpaSyncRun,
    Conversation,
    HermesProfile,
    PhoneIdentity,
    ProactiveSubscription,
    Tenant,
    TenantMembership,
    User,
    WebhookEvent,
    WhatsappMessage,
)
from app.db.session import get_db
from app.jobs.runtime import JobFence, JobLeaseLostError, PermanentJobError, bind_job_fence
from app.providers.meta import MetaProviderError
from app.routes.settings_proactive import router
from app.services import proactive_insights as insights

NOW = datetime(2026, 7, 13, 9, tzinfo=UTC)


@pytest.fixture()
def context():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        whatsapp_backend="meta",
        proactive_insights_enabled=True,
        daily_insights_enabled=True,
        weekly_insights_enabled=True,
        meta_system_user_access_token="synthetic-" + "x" * 40,
        meta_phone_number_id="1234567890",
        meta_waba_id="1234567891",
    )
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        tenant = Tenant(slug="proactive-a", name="A", research_consent_status="withdrawn")
        other_tenant = Tenant(slug="proactive-b", name="B")
        user = User(primary_phone_e164="+2348000012001")
        other = User(primary_phone_e164="+2348000012002")
        db.add_all((tenant, other_tenant, user, other))
        db.flush()
        for business, account, role in (
            (tenant, user, "owner"),
            (tenant, other, "admin"),
            (other_tenant, user, "owner"),
        ):
            db.add(TenantMembership(tenant_id=business.id, user_id=account.id, role=role))
        own = PhoneIdentity(
            tenant_id=tenant.id,
            user_id=user.id,
            phone_e164=user.primary_phone_e164,
            status="approved",
            opt_out=False,
        )
        theirs = PhoneIdentity(
            tenant_id=tenant.id,
            user_id=other.id,
            phone_e164=other.primary_phone_e164,
            status="approved",
            opt_out=False,
        )
        db.add_all((own, theirs))
        db.commit()
        yield db, settings, tenant, other_tenant, user, other, own, theirs
    engine.dispose()


def subscribe(
    context,
    *,
    cadence="daily",
    identity=None,
    timezone="Africa/Lagos",
    hour=8,
    weekday=0,
    expected_revision=0,
    status="active",
):
    db, settings, tenant, _, user, _, own, _ = context
    insights.update_subscription(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        cadence=cadence,
        status=status,
        expected_revision=expected_revision,
        phone_identity_id=(identity or own).id,
        timezone=timezone,
        local_hour=hour,
        weekday=weekday,
        settings=settings,
    )
    db.commit()
    return db.scalar(
        select(ProactiveSubscription).where(
            ProactiveSubscription.tenant_id == tenant.id,
            ProactiveSubscription.user_id == user.id,
            ProactiveSubscription.cadence == cadence,
        )
    )


def data(
    context,
    *,
    now=NOW,
    timezone="Africa/Lagos",
    currency="NGN",
    start=date(2026, 7, 1),
    end=date(2026, 7, 13),
    amounts=(),
):
    db, _, tenant, *_ = context
    connection = BumpaConnection(
        tenant_id=tenant.id,
        encrypted_api_key="synthetic",
        scope_type="business_id",
        scope_id="synthetic",
        provider="bumpa",
        status="active",
        store_timezone=timezone,
        store_currency=currency,
        boundary_revision=1,
        sync_generation=1,
        published_sync_generation=1,
    )
    db.add(connection)
    db.flush()
    run = BumpaSyncRun(
        tenant_id=tenant.id,
        bumpa_connection_id=connection.id,
        boundary_revision=1,
        sync_generation=1,
        status="success",
        completion_quality="complete",
        requested_from=start,
        requested_to=end,
        started_at=now - timedelta(minutes=6),
        finished_at=now - timedelta(minutes=5),
        orders_availability="available",
        orders_count=len(amounts),
        dataset_results={"_bumpa_order_publication": {"version": 1, "state": "complete"}},
    )
    db.add(run)
    db.flush()
    for index, (instant, amount, order_currency) in enumerate(amounts):
        db.add(
            BumpaOrder(
                tenant_id=tenant.id,
                bumpa_order_id=f"synthetic-{index}",
                order_date=instant,
                total_amount=amount,
                currency_code=order_currency,
                raw_payload={"_bumpa_publication": {"sync_run_id": run.id, "state": "active"}},
            )
        )
    db.commit()
    return connection, run


def deliver(context, sub, *, now=NOW, slot=None):
    db, settings, tenant, *_ = context
    return insights.deliver_proactive_insight(
        db,
        tenant_id=tenant.id,
        cadence=sub.cadence,
        slot=slot or insights.schedule_subscription_slot(sub, now),
        settings=settings,
        subscription_id=sub.id,
        subscription_revision=sub.revision,
        now=now,
    )


def fake_meta(monkeypatch, *, error=None, before=None):
    sent = []

    class Fake:
        def send_template(self, phone, **kwargs):
            sent.append((phone, kwargs))
            if error:
                raise error
            return "wamid.synthetic_approved"

    def factory(_settings):
        if before:
            before()
        return Fake()

    monkeypatch.setattr(insights.MetaWhatsAppClient, "from_settings", factory)
    return sent


def test_default_off_and_research_consent_never_creates_subscription(context):
    db, settings, tenant, _, user, *_ = context
    tenant.research_consent_status = "granted"
    db.commit()
    result = insights.subscription_settings(
        db, tenant_id=tenant.id, user_id=user.id, settings=settings
    )
    assert [row["status"] for row in result["subscriptions"]] == ["off", "off"]
    assert all(
        row["phone_identity_id"] is None and row["revision"] == 0 for row in result["subscriptions"]
    )
    assert list(db.scalars(select(ProactiveSubscription))) == []
    assert (
        insights.deliver_proactive_insight(
            db, tenant_id=tenant.id, cadence="daily", slot="2026-07-13", settings=settings, now=NOW
        )["status"]
        == "no_subscription"
    )


@pytest.mark.parametrize("bad", ["other_account", "other_tenant", "opt_out", "revoked"])
def test_only_explicit_own_approved_exact_alias_can_be_selected(context, bad):
    db, _, _, other_tenant, _, _, own, theirs = context
    selected = theirs if bad == "other_account" else own
    if bad == "other_tenant":
        own.tenant_id = other_tenant.id
    elif bad == "opt_out":
        own.opt_out = True
    elif bad == "revoked":
        own.status = "revoked"
    db.commit()
    with pytest.raises(insights.SubscriptionDenied, match="own approved"):
        subscribe(context, identity=selected)
    assert list(db.scalars(select(ProactiveSubscription))) == []


def test_members_cannot_subscribe_and_actor_selection_is_not_delegable(context):
    db, settings, tenant, _, user, *_ = context
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id
        )
    )
    membership.role = "member"
    db.commit()
    with pytest.raises(insights.SubscriptionDenied, match="owner or admin"):
        subscribe(context)
    with pytest.raises(insights.SubscriptionDenied):
        insights.subscription_settings(db, tenant_id=tenant.id, user_id=user.id, settings=settings)


def test_alias_addition_never_inherits_subscription(context, monkeypatch):
    db, _, tenant, _, user, _, own, _ = context
    sub = subscribe(context)
    alias = PhoneIdentity(tenant_id=tenant.id, user_id=user.id, phone_e164="+2348000012099")
    db.add(alias)
    db.commit()
    data(context)
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    assert [phone for phone, _ in sent] == [own.phone_e164]


def test_research_decline_does_not_disable_explicit_business_subscription(context, monkeypatch):
    sub = subscribe(context)
    data(context)
    sent = fake_meta(monkeypatch)
    assert context[2].research_consent_status == "withdrawn"
    assert deliver(context, sub)["sent"] == 1
    assert len(sent) == 1


def test_revision_change_cancels_old_job_and_duplicate_stale_writes(context):
    db, settings, tenant, _, user, *_ = context
    sub = subscribe(context)
    previous_revision = sub.revision
    subscribe(context, status="paused", expected_revision=previous_revision)
    with pytest.raises(insights.SubscriptionConflict):
        subscribe(context, expected_revision=previous_revision)
    result = insights.deliver_proactive_insight(
        db,
        tenant_id=tenant.id,
        cadence="daily",
        slot="2026-07-13",
        settings=settings,
        subscription_id=sub.id,
        subscription_revision=previous_revision,
        now=NOW,
    )
    assert result["sent"] == 0
    assert (
        insights.subscription_settings(db, tenant_id=tenant.id, user_id=user.id, settings=settings)[
            "subscriptions"
        ][0]["status"]
        == "paused"
    )


@pytest.mark.parametrize(
    "revocation", ["pause", "stop", "membership", "alias_owner", "alias_phone", "boundary"]
)
def test_immediate_pre_send_fence_observes_revocation_after_intent_claim(
    context, monkeypatch, revocation
):
    db, _, tenant, _, user, other, own, _ = context
    sub = subscribe(context)
    connection, _ = data(context)

    def revoke():
        if revocation == "pause":
            sub.status = "paused"
            sub.revision += 1
        elif revocation == "stop":
            own.opt_out = True
        elif revocation == "membership":
            db.scalar(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id
                )
            ).status = "revoked"
        elif revocation == "alias_owner":
            own.user_id = other.id
        elif revocation == "alias_phone":
            own.phone_e164 = "+2348000012099"
        else:
            connection.boundary_revision += 1
        db.commit()

    sent = fake_meta(monkeypatch, before=revoke)
    result = deliver(context, sub)
    assert result["sent"] == 0
    assert sent == []
    assert db.scalar(select(WhatsappMessage)).status == "cancelled"


def test_daily_summary_uses_exact_previous_store_day_not_rolling_metrics(context, monkeypatch):
    sub = subscribe(context)
    data(
        context,
        amounts=(
            (datetime(2026, 7, 11, 22, 59, tzinfo=UTC), Decimal("99999"), "NGN"),
            (datetime(2026, 7, 11, 23, 0, tzinfo=UTC), Decimal("123"), "NGN"),
            (datetime(2026, 7, 12, 22, 59, tzinfo=UTC), None, "NGN"),
            (datetime(2026, 7, 12, 23, 0, tzinfo=UTC), Decimal("88888"), "NGN"),
        ),
    )
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    summary = str(sent[0])
    assert "2026-07-12 to 2026-07-12" in summary
    assert "NGN 123.00" in summary and "Orders: 2" in summary
    assert "Orders missing amounts: 1" in summary and "Coverage:" in summary
    assert "99,999" not in summary and "88,888" not in summary


def test_weekly_summary_is_previous_complete_monday_sunday(context, monkeypatch):
    sub = subscribe(context, cadence="weekly")
    data(
        context,
        amounts=(
            (datetime(2026, 7, 5, 23, tzinfo=UTC), Decimal("10"), "NGN"),
            (datetime(2026, 7, 12, 22, 59, tzinfo=UTC), Decimal("20"), "NGN"),
            (datetime(2026, 7, 12, 23, tzinfo=UTC), Decimal("99999"), "NGN"),
        ),
    )
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    assert "2026-07-06 to 2026-07-12" in str(sent[0])
    assert "NGN 30.00" in str(sent[0])


@pytest.mark.parametrize(
    "problem", ["stale", "incomplete_range", "old_generation", "mixed_currency", "missing_currency"]
)
def test_unusable_coverage_currency_or_freshness_never_reaches_provider(
    context, monkeypatch, problem
):
    sub = subscribe(context)
    connection, run = data(
        context, amounts=((datetime(2026, 7, 12, 10, tzinfo=UTC), Decimal("1"), "NGN"),)
    )
    db = context[0]
    if problem == "stale":
        run.finished_at = NOW - timedelta(days=100)
    elif problem == "incomplete_range":
        run.requested_from = date(2026, 7, 13)
    elif problem == "old_generation":
        connection.published_sync_generation = 0
    else:
        db.scalar(select(BumpaOrder)).currency_code = "USD" if problem == "mixed_currency" else None
    db.commit()
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["status"] == "no_fresh_data"
    assert sent == []


def test_duplicates_and_revision_changes_do_not_resend_same_slot(context, monkeypatch):
    sub = subscribe(context)
    data(context)
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    assert deliver(context, sub)["status"] == "already_sent"
    sub = subscribe(context, expected_revision=sub.revision, hour=9)
    assert deliver(context, sub)["status"] == "already_sent"
    assert len(sent) == 1


def test_unknown_provider_acceptance_is_durable_and_never_automatically_retried(
    context, monkeypatch
):
    sub = subscribe(context)
    data(context)
    sent = fake_meta(monkeypatch, error=MetaProviderError("timeout", retryable=True))
    with pytest.raises(PermanentJobError, match="retried safely"):
        deliver(context, sub)
    assert context[0].scalar(select(WhatsappMessage)).status == "ambiguous"
    with pytest.raises(PermanentJobError, match="reconciliation"):
        deliver(context, sub)
    assert len(sent) == 1


@pytest.mark.parametrize(
    "when,hours",
    [(datetime(2026, 3, 9, 12, tzinfo=UTC), 23), (datetime(2026, 11, 2, 13, tzinfo=UTC), 25)],
)
def test_exact_store_day_observes_dst_boundaries(context, when, hours):
    sub = subscribe(context, timezone="America/New_York")
    data(
        context,
        now=when,
        timezone="America/New_York",
        start=when.date() - timedelta(days=8),
        end=when.date(),
    )
    period = insights.insight_period(
        context[0], context[2].id, sub, insights.schedule_subscription_slot(sub, when)
    )
    assert (period.ends_at_utc_exclusive - period.starts_at_utc).total_seconds() == hours * 3600


def test_repeated_dst_hour_produces_one_slot_and_old_queued_day_is_skipped(context, monkeypatch):
    sub = subscribe(context, timezone="America/New_York", hour=1)
    assert insights.schedule_subscription_slot(
        sub, datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    ) == insights.schedule_subscription_slot(sub, datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub, slot="2026-07-12")["status"] == "stale_slot"
    assert sent == []


def test_schedule_validation_and_invalid_iso_week(context):
    with pytest.raises(insights.SubscriptionDenied, match="timezone"):
        subscribe(context, timezone="Not/A_Timezone")
    with pytest.raises(insights.SubscriptionDenied, match="hour"):
        subscribe(context, hour=24)
    assert not insights._valid_slot("weekly", "2025-W53")
    assert not insights._valid_slot("daily", "2026-02-30")


def test_settings_api_is_self_scoped_and_forbids_actor_override(context):
    db, settings, tenant, _, user, _, own, _ = context
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id
        )
    )
    app.dependency_overrides[require_tenant_admin] = lambda: Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        memberships=(membership,),
        platform_roles=frozenset(),
    )
    app.dependency_overrides[get_db] = lambda: db
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        result = client.get("/v1/settings/proactive-insights")
        assert result.status_code == 200
        assert len(result.json()["recipients"]) == 1
        payload = {
            "cadence": "daily",
            "status": "active",
            "expected_revision": 0,
            "phone_identity_id": own.id,
            "timezone": "Africa/Lagos",
            "local_hour": 8,
            "weekday": 0,
        }
        rejected = client.put(
            "/v1/settings/proactive-insights", json={**payload, "user_id": "someone_else"}
        )
        assert rejected.status_code == 422
        assert client.put("/v1/settings/proactive-insights", json=payload).status_code == 200
        assert client.put("/v1/settings/proactive-insights", json=payload).status_code == 409


@pytest.mark.parametrize("scope_change", ["receiver", "lane"])
def test_effect_keys_and_delivery_receipts_are_bound_to_receiver_and_lane(
    context, monkeypatch, scope_change
):
    from app.services.conversation_scope import receiver_scope
    from app.services.whatsapp import _process_delivery

    sub = subscribe(context)
    data(context)
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    db, settings, *_ = context
    altered = settings.model_copy(
        update={"meta_phone_number_id": "1234567892"}
        if scope_change == "receiver"
        else {"app_env": "development"}
    )
    altered_context = (db, altered, *context[2:])
    assert deliver(altered_context, sub)["sent"] == 1
    assert len(sent) == 2
    rows = list(db.scalars(select(WhatsappMessage)))
    assert len({row.idempotency_key for row in rows}) == 2
    assert len({row.receiver_scope for row in rows}) == 2
    for number, active_settings in enumerate((settings, altered)):
        sender = (active_settings.meta_waba_id, active_settings.meta_phone_number_id)
        scope = receiver_scope(active_settings.app_env, sender)
        event = WebhookEvent(
            provider="meta_whatsapp",
            external_event_id=f"synthetic-receipt-{number}",
            signature_valid=True,
            payload={
                "entry": [
                    {
                        "id": sender[0],
                        "changes": [{"value": {"metadata": {"phone_number_id": sender[1]}}}],
                    }
                ]
            },
            processing_status="received",
        )
        db.add(event)
        db.commit()
        _process_delivery(
            db,
            event,
            {"id": "wamid.synthetic_approved", "status": "delivered", "timestamp": "1783933200"},
            active_settings,
        )
        outbound = db.scalar(select(WhatsappMessage).where(WhatsappMessage.receiver_scope == scope))
        assert outbound.status == "delivered"
        assert outbound.payload["receiver_scope"] == scope


def test_lost_worker_epoch_after_claim_cannot_send(context, monkeypatch):
    from app.core.time import utcnow

    sub = subscribe(context)
    data(context)
    db = context[0]
    job = AsyncJob(
        tenant_id=context[2].id,
        kind="whatsapp.proactive_insight",
        payload={},
        idempotency_key="synthetic-claim",
        status="running",
        locked_by="worker-a",
        locked_at=utcnow(),
        claim_epoch=1,
    )
    db.add(job)
    db.commit()
    fence = JobFence(job_id=job.id, worker_id="worker-a", claim_epoch=1, stale_lock_seconds=60)

    def replace_epoch():
        job.claim_epoch = 2
        job.locked_by = "worker-b"
        db.commit()

    sent = fake_meta(monkeypatch, before=replace_epoch)
    with bind_job_fence(fence), pytest.raises(JobLeaseLostError):
        deliver(context, sub)
    assert sent == []
    assert fence.invalid
    assert db.scalar(select(WhatsappMessage)).status == "sending"


@pytest.mark.parametrize("missing", ["run_marker", "row_marker", "superseded", "unmapped_date"])
def test_unverified_publication_membership_never_claims_complete_current_totals(
    context, monkeypatch, missing
):
    sub = subscribe(context)
    _, run = data(
        context, amounts=((datetime(2026, 7, 12, 10, tzinfo=UTC), Decimal("100"), "NGN"),)
    )
    db = context[0]
    order = db.scalar(select(BumpaOrder))
    if missing == "run_marker":
        run.dataset_results = {}
    elif missing == "row_marker":
        order.raw_payload = {}
    elif missing == "superseded":
        order.raw_payload = {"_bumpa_publication": {"sync_run_id": run.id, "state": "superseded"}}
    else:
        order.order_date = None
    db.commit()
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["status"] == "no_fresh_data"
    assert sent == []


def test_verified_complete_empty_resync_never_attributes_old_orders_to_current_run(
    context, monkeypatch
):
    sub = subscribe(context)
    connection, old_run = data(
        context, amounts=((datetime(2026, 7, 12, 10, tzinfo=UTC), Decimal("100"), "NGN"),)
    )
    db = context[0]
    connection.sync_generation = connection.published_sync_generation = 2
    current_run = BumpaSyncRun(
        tenant_id=context[2].id,
        bumpa_connection_id=connection.id,
        boundary_revision=1,
        sync_generation=2,
        status="success",
        completion_quality="complete",
        requested_from=old_run.requested_from,
        requested_to=old_run.requested_to,
        started_at=NOW - timedelta(minutes=3),
        finished_at=NOW - timedelta(minutes=2),
        orders_availability="available",
        orders_count=0,
        dataset_results={"_bumpa_order_publication": {"version": 1, "state": "complete"}},
    )
    db.add(current_run)
    db.commit()
    # The inherited upsert-only storage may still contain this old row. Its
    # previous publication membership can never be mistaken for the empty pull.
    assert db.scalar(select(BumpaOrder)).total_amount == Decimal("100")
    sent = fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    assert "Orders: 0" in str(sent[0]) and "NGN 0.00" in str(sent[0])
    assert "NGN 100" not in str(sent[0])


def test_successful_delivery_is_one_scheduled_canonical_event_without_synthetic_user(
    context, monkeypatch
):
    sub = subscribe(context)
    data(context)
    fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    assert deliver(context, sub)["status"] == "already_sent"
    db, settings, tenant, _, user, *_ = context
    events = list(db.scalars(select(AgentMessage)))
    assert len(events) == 1
    event = events[0]
    transport = db.scalar(select(WhatsappMessage))
    assert event.id == transport.id
    assert event.direction == "outbound" and event.initiating_message_id is None
    assert event.content == transport.payload["summary"]
    assert event.content == transport.text_body
    assert event.external_message_id == transport.meta_message_id
    assert event.event_scope == transport.receiver_scope
    assert event.media_metadata["event_kind"] == "scheduled"
    assert event.media_metadata["delivery_status"] == "sent"
    assert event.media_metadata["subscription_id"] == sub.id
    assert event.media_metadata["period"] == transport.payload["period"]
    assert event.media_metadata["source_run_id"] == transport.payload["source_run_id"]
    conversation = db.get(Conversation, event.conversation_id)
    assert conversation.tenant_id == tenant.id and conversation.user_id == user.id
    assert conversation.context_generation == 1 and conversation.status == "open"
    assert conversation.receiver_waba_id == settings.meta_waba_id
    assert conversation.receiver_phone_id == settings.meta_phone_number_id
    assert conversation.environment_lane == settings.app_env


@pytest.mark.parametrize("with_reply", [False, True])
def test_real_followup_path_sees_delivered_summary_once_and_reply_to_can_find_it(
    context, monkeypatch, with_reply
):
    from app.providers.hermes import HermesResult
    from app.services import chat, whatsapp

    db, settings, tenant, other_tenant, user, other, own, theirs = context
    settings = settings.model_copy(update={"agent_backend": "hermes"})
    context = (db, settings, tenant, other_tenant, user, other, own, theirs)
    profile = HermesProfile(
        tenant_id=tenant.id,
        profile_name="synthetic-proactive-followup",
        provider="hermes",
        profile_path="/synthetic",
        api_port=8700,
        api_internal_url="https://synthetic.invalid/v1",
        status="active",
        encrypted_api_key=FieldCipher.from_settings(settings).encrypt("synthetic-key"),
    )
    db.add(profile)
    db.commit()
    sub = subscribe(context)
    data(context)
    fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    scheduled = db.scalar(select(AgentMessage))
    summary_prefix = "Bumpa order summary 2026-07-12 to 2026-07-12"
    assert list(db.scalars(select(AgentMessage).where(AgentMessage.direction == "inbound"))) == []
    captured = []

    class FakeHermes:
        def respond(self, endpoint, *, message, business_context, history=None):
            captured.append({"message": message, "history": history})
            return HermesResult(
                content="This explains the dated order summary.",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
            )

    class FakeInboundMeta:
        def send_text(self, phone, body, **kwargs):
            return "wamid.synthetic_followup_reply"

    monkeypatch.setattr(chat, "HermesClient", lambda _settings: FakeHermes())
    monkeypatch.setattr(
        whatsapp.MetaWhatsAppClient,
        "for_inbound_reply",
        lambda _settings, **kwargs: FakeInboundMeta(),
    )
    incoming = {
        "id": "wamid.synthetic_actual_followup",
        "from": own.phone_e164.lstrip("+"),
        "type": "text",
        "text": {"body": "What does this mean?"},
    }
    if with_reply:
        incoming["context"] = {"id": scheduled.external_message_id}
    event = WebhookEvent(
        provider="meta_whatsapp",
        external_event_id="synthetic-followup-event",
        signature_valid=True,
        payload={
            "entry": [
                {
                    "id": settings.meta_waba_id,
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": settings.meta_phone_number_id},
                                "messages": [incoming],
                            }
                        }
                    ],
                }
            ]
        },
        processing_status="received",
    )
    db.add(event)
    db.commit()
    whatsapp._process_message(db, event, incoming, settings)
    assert len(captured) == 1
    assert sum(summary_prefix in item["content"] for item in captured[0]["history"]) == 1
    assert any(
        item["role"] == "assistant" and summary_prefix in item["content"]
        for item in captured[0]["history"]
    )
    if with_reply:
        assert "This replies to the earlier message" in captured[0]["message"]
        assert summary_prefix in captured[0]["message"]
    actual_inputs = list(
        db.scalars(select(AgentMessage).where(AgentMessage.direction == "inbound"))
    )
    assert len(actual_inputs) == 1
    assert actual_inputs[0].external_message_id == incoming["id"]


def test_scheduled_event_uses_exact_scope_and_never_reopens_legacy_or_other_receiver(
    context, monkeypatch
):
    from app.services.chat import _conversation_history

    db, settings, tenant, _, user, other, *_ = context
    scopes = [
        {
            "user_id": user.id,
            "context_generation": 0,
            "receiver_phone_id": settings.meta_phone_number_id,
        },
        {"user_id": user.id, "context_generation": 1, "receiver_phone_id": "another-receiver"},
        {
            "user_id": other.id,
            "context_generation": 1,
            "receiver_phone_id": settings.meta_phone_number_id,
        },
    ]
    unrelated = []
    for values in scopes:
        conversation = Conversation(
            tenant_id=tenant.id,
            channel="whatsapp",
            status="open",
            environment_lane=settings.app_env,
            receiver_waba_id=settings.meta_waba_id,
            **values,
        )
        db.add(conversation)
        unrelated.append(conversation)
    db.commit()
    sub = subscribe(context)
    data(context)
    fake_meta(monkeypatch)
    assert deliver(context, sub)["sent"] == 1
    scheduled = db.scalar(select(AgentMessage))
    assert scheduled.conversation_id not in {row.id for row in unrelated}
    for conversation in unrelated:
        assert (
            _conversation_history(
                db, tenant_id=tenant.id, conversation_id=conversation.id, limit=20
            )
            == []
        )


def test_transport_receipt_and_scheduled_ledger_commit_atomically_after_acceptance(
    context, monkeypatch
):
    sub = subscribe(context)
    data(context)
    sent = fake_meta(monkeypatch)
    original = insights._record_scheduled_message

    def fail_transaction(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Synthetic final transaction failure")

    monkeypatch.setattr(insights, "_record_scheduled_message", fail_transaction)
    with pytest.raises(RuntimeError, match="transaction failure"):
        deliver(context, sub)
    db = context[0]
    db.rollback()
    assert db.scalar(select(WhatsappMessage)).status == "sending"
    assert list(db.scalars(select(AgentMessage))) == []
    with pytest.raises(PermanentJobError, match="reconciliation"):
        deliver(context, sub)
    assert len(sent) == 1
