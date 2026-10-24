from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import (
    AgentMessage,
    BumpaConnection,
    BumpaSyncRun,
    Conversation,
    Tenant,
    TenantMembership,
    User,
)
from app.services.agent_actions import (
    AgentActionError,
    create_action_context_token,
    decode_action_context_token,
)
from app.services.business_onboarding import (
    KEYS,
    OnboardingCommand,
    apply_command,
    chat_reply,
    order_hint,
    propose_update,
    state_for,
    view,
)
from app.services.chat import build_business_context

ANSWERS: dict[str, Any] = {
    "offering": "Women’s shoes and repairs",
    "customers": "Working women in Ibadan",
    "channels": ["WhatsApp", "Physical shop"],
    "monthly_orders": "It varies a lot / I’m not sure",
    "team_size": "2–3 people",
    "goal": "Get more customers",
    "challenge": "People ask for prices but delivery costs put them off",
}


@pytest.fixture()
def business() -> Generator[tuple[Session, Tenant, User], None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(slug="ibadan-shoes", name="Ibadan Shoes")
        user = User(primary_phone_e164="+2348000000123")
        db.add_all([tenant, user])
        db.flush()
        db.add(
            TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner", status="active")
        )
        db.commit()
        yield db, tenant, user
    engine.dispose()


def command(db: Session, tenant: Tenant, user: User, action: str, **values: Any) -> None:
    apply_command(
        db,
        tenant,
        user.id,
        OnboardingCommand.model_validate(
            {"action": action, "expected_revision": state_for(db, tenant)["revision"], **values}
        ),
    )


def complete(db: Session, tenant: Tenant, user: User) -> None:
    command(db, tenant, user, "start")
    for key, value in ANSWERS.items():
        command(db, tenant, user, "answer", field=key, value=value)
    assert state_for(db, tenant)["status"] == "review"
    command(db, tenant, user, "complete")
    db.commit()


def test_review_required_and_profile_available_without_bumpa(
    business: tuple[Session, Tenant, User],
) -> None:
    db, tenant, user = business
    command(db, tenant, user, "start")
    command(db, tenant, user, "answer", field="offering", value=ANSWERS["offering"])
    assert state_for(db, tenant)["answers"] == {}
    assert ANSWERS["offering"] not in build_business_context(db, tenant.id)[0]
    with pytest.raises(HTTPException, match="Review your seven"):
        command(db, tenant, user, "complete")
    for key in KEYS[1:]:
        command(db, tenant, user, "answer", field=key, value=ANSWERS[key])
    command(db, tenant, user, "complete")
    db.commit()
    db.expire_all()
    context, freshness = build_business_context(db, tenant.id)
    assert "Ibadan Shoes" in context and "Working women in Ibadan" in context
    assert "delivery costs" in context and "2–3 people" in context
    assert "No synced Bumpa metrics" in context and freshness is None
    other = Tenant(slug="other", name="Other")
    db.add(other)
    db.flush()
    assert "Working women" not in build_business_context(db, other.id)[0]
    assert tenant.research_consent_status == "pending"


def test_validation_resume_and_stale_channel_rejected(
    business: tuple[Session, Tenant, User],
) -> None:
    db, tenant, user = business
    command(db, tenant, user, "start")
    stale = state_for(db, tenant)["revision"]
    command(db, tenant, user, "answer", field="offering", value=ANSWERS["offering"])
    with pytest.raises(HTTPException, match="another chat"):
        apply_command(
            db,
            tenant,
            user.id,
            OnboardingCommand(
                action="answer", expected_revision=stale, field="offering", value="Stale"
            ),
        )
    command(db, tenant, user, "pause")
    db.commit()
    db.expire_all()
    command(db, tenant, user, "resume")
    assert state_for(db, tenant)["step"] == "customers"
    command(db, tenant, user, "answer", field="customers", value=ANSWERS["customers"])
    for value in [[], ["Unlisted"], ["Other"]]:
        with pytest.raises(HTTPException):
            command(db, tenant, user, "answer", field="channels", value=value)
    command(
        db,
        tenant,
        user,
        "answer",
        field="channels",
        value=["WhatsApp", "Other"],
        detail="Market stall",
    )
    assert state_for(db, tenant)["draft"]["channels"]["detail"] == "Market stall"


def test_whatsapp_numbered_answers_and_interruptions(
    business: tuple[Session, Tenant, User],
) -> None:
    db, tenant, user = business

    def reply(text: str) -> str | None:
        return chat_reply(db, tenant, user.id, text, channel="whatsapp")

    assert "one at a time" in (reply("Hi") or "")
    assert "Question 1 of 7" in (reply("LET’S START") or "")
    assert reply("How can I get more customers?") is None
    assert state_for(db, tenant)["status"] == "paused"
    assert state_for(db, tenant)["draft"] == {}
    reply("RESUME ONBOARDING")
    assert reply("Abeg help me price my cakes") is None
    assert state_for(db, tenant)["status"] == "paused"
    assert state_for(db, tenant)["draft"] == {}
    reply("RESUME ONBOARDING")
    reply(ANSWERS["offering"])
    reply(ANSWERS["customers"])
    assert "Question 4 of 7" in (reply("1, 6") or "")
    assert state_for(db, tenant)["draft"]["channels"] == ["WhatsApp", "Physical shop"]
    reply("6")
    reply("2")
    reply("1")
    review = reply(ANSWERS["challenge"]) or ""
    assert "EDIT 1–7" in review and "2–3 people" in review
    reply("EDIT 5")
    assert "Reply SAVE" in (reply("1") or "")
    assert "How can I help" in (reply("SAVE") or "")
    assert state_for(db, tenant)["answers"]["team_size"] == "Just me (1 person)"


def test_order_prefill_requires_exact_complete_current_month_boundary(
    business: tuple[Session, Tenant, User],
) -> None:
    db, tenant, _user = business
    assert order_hint(db, tenant) is None
    connection = BumpaConnection(
        tenant_id=tenant.id,
        encrypted_api_key="fixture",
        scope_type="business_id",
        scope_id="fixture",
        provider="local",
        status="active",
    )
    db.add(connection)
    db.flush()
    end = utcnow().astimezone(ZoneInfo(tenant.timezone)).date().replace(day=1) - timedelta(days=1)
    run = BumpaSyncRun(
        tenant_id=tenant.id,
        bumpa_connection_id=connection.id,
        requested_from=end.replace(day=1),
        requested_to=end,
        status="success",
        completion_quality="complete",
        orders_availability="available",
        orders_count=18,
        finished_at=utcnow(),
    )
    db.add(run)
    db.flush()
    hint = order_hint(db, tenant)
    assert hint and hint["value"] == "6–20" and "outside Bumpa" in hint["note"]
    run.boundary_revision = connection.boundary_revision + 1
    db.flush()
    assert order_hint(db, tenant) is None
    run.boundary_revision = connection.boundary_revision
    run.orders_availability = "unavailable"
    db.flush()
    assert order_hint(db, tenant) is None


def test_profile_update_exact_confirmation_scope_expiry_and_visibility(
    business: tuple[Session, Tenant, User],
) -> None:
    db, tenant, user = business
    complete(db, tenant, user)
    conversation = Conversation(
        tenant_id=tenant.id, user_id=user.id, channel="whatsapp", title="Team changed"
    )
    db.add(conversation)
    db.flush()
    inbound = AgentMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel="whatsapp",
        direction="inbound",
        content="I hired two people",
        redacted_content="I hired two people",
    )
    db.add(inbound)
    db.flush()
    token = create_action_context_token(
        get_settings(),
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        initiating_message_id=inbound.id,
        channel="whatsapp",
        purpose="business_profile",
    )
    proposal = propose_update(
        db, tenant.id, changes={"team_size": "4–5 people"}, action_context_token=token
    )
    with pytest.raises(AgentActionError):
        decode_action_context_token(get_settings(), token, expected_tenant_id=tenant.id)
    assert proposal["status"] == "awaiting_confirmation"
    state = state_for(db, tenant)
    code = state["pending_update"]["confirmation_code"]
    assert state["answers"]["team_size"] == "2–3 people"
    assert "pending_update" not in view(db, tenant, editable=False, user_id="staff")
    assert "pending_update" not in view(db, tenant, editable=True, user_id="other-owner")
    assert set(view(db, tenant, editable=True, user_id=user.id)["pending_update"]) == {"changes"}
    assert "no longer available" in (
        chat_reply(
            db, tenant, user.id, f"save profile {code}", channel="web", conversation_id="wrong"
        )
        or ""
    )
    second = propose_update(
        db, tenant.id, changes={"team_size": "6–10 people"}, action_context_token=token
    )
    assert "6–10 people" in second["preview"]
    assert "no longer available" in (
        chat_reply(
            db,
            tenant,
            user.id,
            f"save profile {code}",
            channel="whatsapp",
            conversation_id=conversation.id,
        )
        or ""
    )
    code = state_for(db, tenant)["pending_update"]["confirmation_code"]
    assert "updated" in (
        chat_reply(
            db,
            tenant,
            user.id,
            f"save profile {code}",
            channel="whatsapp",
            conversation_id=conversation.id,
        )
        or ""
    )
    assert state_for(db, tenant)["answers"]["team_size"] == "6–10 people"
    command(db, tenant, user, "edit", field="offering")
    command(db, tenant, user, "answer", field="offering", value="Shoes and bags")
    command(db, tenant, user, "pause")
    propose_update(db, tenant.id, changes={"team_size": "4–5 people"}, action_context_token=token)
    command(db, tenant, user, "confirm_update")
    assert state_for(db, tenant)["draft"]["team_size"] == "4–5 people"
    assert state_for(db, tenant)["draft"]["offering"] == "Shoes and bags"
    propose_update(
        db, tenant.id, changes={"team_size": "Just me (1 person)"}, action_context_token=token
    )
    from app.services.business_onboarding import save_state

    state = state_for(db, tenant)
    state["pending_update"]["expires_at"] = 0
    save_state(db, tenant.id, state)
    with pytest.raises(HTTPException, match="no longer available"):
        command(db, tenant, user, "confirm_update")
    assert state_for(db, tenant)["answers"]["team_size"] == "4–5 people"


def test_member_cannot_start_setup(business: tuple[Session, Tenant, User]) -> None:
    db, tenant, user = business
    membership = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id))
    assert membership
    membership.role = "member"
    db.flush()
    assert chat_reply(db, tenant, user.id, "onboarding", channel="web") is None
    assert state_for(db, tenant)["status"] == "not_started"


def test_business_profile_api_authorization_and_schema(client: Any) -> None:
    from app.db.models import MerchantBusinessProfile
    from app.db.session import SessionLocal
    from tests.conftest import auth_headers

    owner = auth_headers(client, "+2348012345678")
    other = auth_headers(client, "+2348012345679")
    tenant_id = client.get("/v1/auth/me", headers=owner).json()["current_tenant_id"]
    other_id = client.get("/v1/auth/me", headers=other).json()["current_tenant_id"]
    assert (
        client.get(
            "/v1/settings/business-profile", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    denied = client.get("/v1/settings/business-profile", headers={**owner, "X-Tenant-ID": other_id})
    assert denied.status_code == 403
    with SessionLocal() as db:
        old = db.scalar(
            select(MerchantBusinessProfile).where(MerchantBusinessProfile.tenant_id == tenant_id)
        )
        previous = dict(old.state) if old else None
    try:
        initial = client.get("/v1/settings/business-profile", headers=owner).json()
        assert len(initial["questions"]) == 7 and initial["can_edit"] is True
        response = client.post(
            "/v1/settings/business-profile",
            headers=owner,
            json={"action": "start", "expected_revision": initial["revision"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["step"] == "offering"
        conflict = client.post(
            "/v1/settings/business-profile",
            headers=owner,
            json={"action": "start", "expected_revision": initial["revision"]},
        )
        assert conflict.status_code == 409
        assert client.get("/v1/settings/business-profile", headers=other).json()["revision"] == 0
    finally:
        with SessionLocal() as db:
            row = db.scalar(
                select(MerchantBusinessProfile).where(
                    MerchantBusinessProfile.tenant_id == tenant_id
                )
            )
            if row and previous is None:
                db.delete(row)
            elif row:
                row.state = previous
            db.commit()
