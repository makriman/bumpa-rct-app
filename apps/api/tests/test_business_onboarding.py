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
    HermesProfile,
    Tenant,
    TenantMembership,
    User,
)
from app.services import chat as chat_service
from app.services.agent_actions import (
    AgentActionError,
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
from tests.run_fixture import active_context

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


def test_whatsapp_natural_setup_pidgin_pause_and_useful_first_step(
    business: tuple[Session, Tenant, User],
) -> None:
    db, tenant, user = business

    def reply(text: str) -> str | None:
        return chat_reply(db, tenant, user.id, text, channel="whatsapp")

    assert "business help straight away" in (reply("LATER") or "")
    assert state_for(db, tenant)["status"] == "not_started"
    assert "Question 1 of 7" in (reply("make we start") or "")
    reply("I dey sell food and cater for events")
    assert "saved so far" in (reply("make we do am later") or "")
    assert state_for(db, tenant)["answers"] == {}
    assert "Question 2 of 7" in (reply("make we continue") or "")
    reply("Office workers and families near my stall")
    reply("WhatsApp and Physical shop")
    assert state_for(db, tenant)["draft"]["channels"] == ["WhatsApp", "Physical shop"]
    reply("e dey vary")
    reply("na only me")
    reply("i wan get more customers")
    review = reply("Transport costs dey make customers change mind") or ""
    assert "1. Products or services:" in review
    assert "7. What’s getting in the way:" in review
    assert "EDIT 1–7" in review
    assert state_for(db, tenant)["answers"] == {}
    saved = reply("SAVE") or ""
    assert "one clear offer" in saved and saved.count("?") == 1
    assert len(saved) < 450
    assert state_for(db, tenant)["answers"]["team_size"] == "Just me (1 person)"
    assert state_for(db, tenant)["answers"]["monthly_orders"] == "It varies a lot / I’m not sure"
    assert tenant.research_consent_status == "pending"


@pytest.mark.parametrize(
    "message",
    [
        "Abeg help me price my cakes",
        "Wetin I fit do to get more customers",
        "How I fit get more customers",
        "Please help me price my cakes",
    ],
)
def test_unpunctuated_help_interrupts_without_becoming_a_fact(
    business: tuple[Session, Tenant, User],
    message: str,
) -> None:
    db, tenant, user = business
    command(db, tenant, user, "start")
    assert chat_reply(db, tenant, user.id, message, channel="whatsapp") is None
    state = state_for(db, tenant)
    assert state["status"] == "paused"
    assert state["draft"] == {}
    assert state["answers"] == {}


def test_interrupted_onboarding_reply_rolls_back_answer_before_retry(
    business: tuple[Session, Tenant, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, tenant, user = business
    db.add(
        HermesProfile(
            tenant_id=tenant.id,
            profile_name="review-atomic",
            profile_path="/profiles/review-atomic",
            api_port=8700,
            provider="local",
            status="active",
            encrypted_api_key="local-review-only",
        )
    )
    command(db, tenant, user, "start")
    db.commit()
    original = chat_service._record_successful_chat_events

    def interrupted(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Simulated interruption before the reply is durable")

    monkeypatch.setattr(chat_service, "_record_successful_chat_events", interrupted)
    with pytest.raises(RuntimeError, match="Simulated interruption"):
        chat_service.handle_chat(
            db,
            tenant=tenant,
            user=user,
            message=ANSWERS["offering"],
            channel="whatsapp",
            external_message_id="review-atomic-answer",
        )
    db.rollback()
    assert state_for(db, tenant)["step"] == "offering"
    assert state_for(db, tenant)["draft"] == {}
    assert (
        db.scalar(
            select(AgentMessage.id).where(
                AgentMessage.external_message_id == "review-atomic-answer",
            )
        )
        is None
    )

    monkeypatch.setattr(chat_service, "_record_successful_chat_events", original)
    _, _, outgoing, _ = chat_service.handle_chat(
        db,
        tenant=tenant,
        user=user,
        message=ANSWERS["offering"],
        channel="whatsapp",
        external_message_id="review-atomic-answer",
    )
    assert state_for(db, tenant)["step"] == "customers"
    assert state_for(db, tenant)["draft"]["offering"] == ANSWERS["offering"]
    assert "Question 2 of 7" in outgoing.content


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
    token = active_context(
        db,
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


@pytest.mark.parametrize(
    "media_type", ["image", "sticker", "video", "document", "location", "contacts"]
)
def test_attachment_during_setup_reaches_agent_without_becoming_a_profile_fact(
    business: tuple[Session, Tenant, User],
    monkeypatch: pytest.MonkeyPatch,
    media_type: str,
) -> None:
    db, tenant, user = business
    db.add(
        HermesProfile(
            tenant_id=tenant.id,
            profile_name="media-review",
            profile_path="/profiles/media-review",
            api_port=8700,
            provider="local",
            status="active",
            encrypted_api_key="local-test-only",
        )
    )
    command(db, tenant, user, "start")
    command(db, tenant, user, "answer", field="offering", value=ANSWERS["offering"])
    db.commit()
    seen: list[str] = []

    def respond(_self: Any, _profile: str, message: str, _context: str) -> str:
        seen.append(message)
        return "Synthetic attachment answer."

    monkeypatch.setattr(chat_service.LocalAgentRuntime, "respond", respond)
    prompt = "Please examine this image and help with the business task it shows."
    _, _, outgoing, _ = chat_service.handle_chat(
        db,
        tenant=tenant,
        user=user,
        message=prompt,
        channel="whatsapp",
        external_message_id=f"media-review-{media_type}",
        media_metadata={"type": media_type},
        provider_content_parts=[{"type": "text", "text": prompt}],
    )
    assert seen == [prompt]
    assert outgoing.content == "Synthetic attachment answer."
    state = state_for(db, tenant)
    assert state["status"] == "paused"
    assert state["step"] == "customers"
    assert state["draft"] == {"offering": ANSWERS["offering"]}
    assert state["answers"] == {}


@pytest.mark.parametrize("media_type", ["audio", "voice"])
def test_transcribed_voice_note_can_answer_a_setup_question(
    business: tuple[Session, Tenant, User],
    media_type: str,
) -> None:
    db, tenant, user = business
    command(db, tenant, user, "start")
    reply = chat_reply(
        db, tenant, user.id, ANSWERS["offering"], channel="whatsapp", media_type=media_type
    )
    assert reply is not None and "Question 2 of 7" in reply
    state = state_for(db, tenant)
    assert state["status"] == "in_progress"
    assert state["draft"] == {"offering": ANSWERS["offering"]}


@pytest.mark.parametrize("completed", [False, True])
def test_attachment_caption_does_not_execute_a_setup_command(
    business: tuple[Session, Tenant, User],
    completed: bool,
) -> None:
    db, tenant, user = business
    if completed:
        complete(db, tenant, user)
    before = view(db, tenant, editable=True, user_id=user.id)
    assert (
        chat_reply(db, tenant, user.id, "onboarding", channel="whatsapp", media_type="document")
        is None
    )
    after = view(db, tenant, editable=True, user_id=user.id)
    assert after["status"] == before["status"]
    assert after["revision"] == before["revision"]
    assert after["answers"] == before["answers"]
