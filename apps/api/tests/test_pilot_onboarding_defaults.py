from __future__ import annotations

import io
import json
import sys
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.cli.arm_pilot_onboarding import main as pilot_cli
from app.cli.arm_pilot_onboarding import validate_snapshot
from app.core.config import get_settings
from app.core.crypto import secret_hash
from app.db.models import AgentMessage, HermesProfile, PhoneIdentity, WebhookEvent
from app.providers.local import LocalMessagingProvider
from app.services import whatsapp
from app.services.business_onboarding import arm_pilot_inbound_defaults, chat_reply, state_for
from tests import test_business_onboarding as onboarding_tests
from tests.test_business_onboarding import ANSWERS, command, complete


@pytest.fixture()
def business() -> Any:
    yield from onboarding_tests.business.__wrapped__()


def captured(db: Any, tenant: Any, user: Any, *, secondary: bool = False) -> dict[str, str]:
    identity = PhoneIdentity(
        tenant_id=tenant.id,
        user_id=user.id,
        status="approved",
        phone_e164="+2348000000124" if secondary else user.primary_phone_e164,
    )
    db.add(identity)
    db.flush()
    return {
        "identity_id": identity.id,
        "tenant_id": tenant.id,
        "user_id": user.id,
        "role": "owner",
        "phone_fingerprint": secret_hash(
            "onboarding-existing-pilot:" + identity.phone_e164, get_settings().otp_secret
        ),
    }


def arm(db: Any, tenant: Any, user: Any, *, secondary: bool = False) -> dict[str, str]:
    row = captured(db, tenant, user, secondary=secondary)
    assert arm_pilot_inbound_defaults(db, [row], get_settings().otp_secret)["armed"] == 1
    db.commit()
    return row


def reply(
    db: Any, tenant: Any, user: Any, row: dict[str, str], message: str, **kwargs: Any
) -> str | None:
    return chat_reply(
        db,
        tenant,
        user.id,
        message,
        channel="whatsapp",
        whatsapp_identity=(row["identity_id"], row["phone_fingerprint"]),
        **kwargs,
    )


def test_first_inbound_starts_then_accepts_only_the_next_answer(business: Any) -> None:
    db, tenant, user = business
    row = arm(db, tenant, user)
    assert "Question 1 of 7" in str(reply(db, tenant, user, row, "Hi"))
    assert state_for(db, tenant)["draft"] == {}
    assert "Question 2 of 7" in str(reply(db, tenant, user, row, ANSWERS["offering"]))
    assert state_for(db, tenant)["draft"] == {"offering": ANSWERS["offering"]}
    revision = state_for(db, tenant)["revision"]
    assert (
        arm_pilot_inbound_defaults(db, [row], get_settings().otp_secret)["already_configured"] == 1
    )
    assert state_for(db, tenant)["revision"] == revision


def test_web_partial_setup_accepts_first_answer_and_secondary_is_captured(
    business: Any,
) -> None:
    db, tenant, user = business
    command(db, tenant, user, "start")
    command(db, tenant, user, "answer", field="offering", value=ANSWERS["offering"])
    row = arm(db, tenant, user, secondary=True)
    assert "Question 3 of 7" in str(reply(db, tenant, user, row, ANSWERS["customers"]))
    assert state_for(db, tenant)["draft"]["offering"] == ANSWERS["offering"]
    assert state_for(db, tenant)["draft"]["customers"] == ANSWERS["customers"]


def test_completed_business_is_not_reset_or_rearmed(business: Any) -> None:
    db, tenant, user = business
    complete(db, tenant, user)
    before = deepcopy(state_for(db, tenant))
    row = captured(db, tenant, user)
    assert (
        arm_pilot_inbound_defaults(db, [row], get_settings().otp_secret)["completed_preserved"] == 1
    )
    assert reply(db, tenant, user, row, "How do I improve my price?") is None
    assert state_for(db, tenant) == before


@pytest.mark.parametrize(
    "media_type", ["image", "document", "video", "location", "contacts", "sticker"]
)
def test_first_attachment_does_not_consume_default_or_save_media(
    business: Any, media_type: str
) -> None:
    db, tenant, user = business
    row = arm(db, tenant, user)
    assert (
        reply(db, tenant, user, row, "Generated attachment prompt", media_type=media_type) is None
    )
    state = state_for(db, tenant)
    assert state["draft"] == {} and state["status"] == "not_started"
    assert state["whatsapp_first_inbound"][row["identity_id"]]["pending"] is True
    assert "Question 1 of 7" in str(reply(db, tenant, user, row, "hi"))


@pytest.mark.parametrize("media_type", ["audio", "voice"])
def test_transcribed_voice_starts_then_continues_setup(business: Any, media_type: str) -> None:
    db, tenant, user = business
    row = arm(db, tenant, user)
    assert "Question 1 of 7" in str(reply(db, tenant, user, row, "hello", media_type=media_type))
    assert "Question 2 of 7" in str(
        reply(db, tenant, user, row, ANSWERS["offering"], media_type=media_type)
    )


@pytest.mark.parametrize(
    "message", ["How can I price my repairs?", "abeg help me price my repairs"]
)
def test_immediate_business_help_interrupts_optional_default(business: Any, message: str) -> None:
    db, tenant, user = business
    row = arm(db, tenant, user)
    assert reply(db, tenant, user, row, message) is None
    assert state_for(db, tenant)["status"] == "paused"
    assert state_for(db, tenant)["draft"] == {}


def test_unknown_future_or_changed_identity_does_not_receive_default(business: Any) -> None:
    db, tenant, user = business
    row = arm(db, tenant, user)
    assert (
        reply(db, tenant, user, {**row, "identity_id": "not-captured"}, "ordinary message") is None
    )
    assert (
        reply(db, tenant, user, {**row, "phone_fingerprint": "0" * 64}, "ordinary message") is None
    )
    assert state_for(db, tenant)["status"] == "not_started"


def test_mapping_drift_aborts_before_any_defaults_are_written(business: Any) -> None:
    db, tenant, user = business
    first = captured(db, tenant, user)
    bad = {**first, "phone_fingerprint": "0" * 64}
    with pytest.raises(ValueError, match="identity changed"):
        arm_pilot_inbound_defaults(db, [first, bad], get_settings().otp_secret)
    assert state_for(db, tenant)["revision"] == 0


def test_snapshot_rejects_raw_numbers_duplicate_identities_and_wrong_cohort(business: Any) -> None:
    db, tenant, user = business
    row = captured(db, tenant, user)
    payload = {"cohort": "existing-pilot-2026-10-24", "identity_count": 1, "identities": [row]}
    assert validate_snapshot(payload) == [row]
    for bad in [
        {**payload, "cohort": "future"},
        {**payload, "identities": [{**row, "phone": user.primary_phone_e164}]},
        {**payload, "identities": [row, row], "identity_count": 2},
    ]:
        with pytest.raises(ValueError):
            validate_snapshot(bad)


def test_duplicate_whatsapp_inbound_does_not_advance_or_send_twice(
    business: Any, monkeypatch: Any
) -> None:
    db, tenant, user = business
    arm(db, tenant, user)
    db.add(
        HermesProfile(
            tenant_id=tenant.id,
            profile_name="pilot-default-proof",
            profile_path="/profiles/pilot-default-proof",
            api_port=8700,
            provider="local",
            status="active",
            encrypted_api_key="local-test-only",
        )
    )
    event = WebhookEvent(
        provider="whatsapp", external_event_id="pilot-first-proof", signature_valid=True, payload={}
    )
    db.add(event)
    db.commit()
    delivered = []
    monkeypatch.setattr(
        LocalMessagingProvider,
        "send_text",
        lambda _self, _phone, body: delivered.append(body) or "wamid.synthetic-proof",
    )
    monkeypatch.setattr(whatsapp, "consume_operation_rate_limit", lambda *a, **k: None)
    settings = get_settings().model_copy(
        update={"whatsapp_backend": "mock", "whatsapp_progress_enabled": False}
    )
    inbound = {
        "id": "wamid.first-inbound-proof",
        "from": user.primary_phone_e164.lstrip("+"),
        "type": "text",
        "text": {"body": "hi"},
    }
    whatsapp._process_message(db, event, inbound, settings)
    revision = state_for(db, tenant)["revision"]
    whatsapp._process_message(db, event, inbound, settings)
    assert state_for(db, tenant)["revision"] == revision
    assert len(delivered) == 1 and "Question 1 of 7" in delivered[0]
    assert (
        len(db.scalars(select(AgentMessage).where(AgentMessage.direction == "inbound")).all()) == 1
    )
    assert tenant.research_consent_status == "pending"


def test_operator_cli_arms_without_network_or_new_mappings(
    business: Any, monkeypatch: Any, capsys: Any
) -> None:
    db, tenant, user = business
    row = captured(db, tenant, user)
    payload = {"cohort": "existing-pilot-2026-10-24", "identity_count": 1, "identities": [row]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["pilot-cli", "--apply"])
    settings = get_settings().model_copy(
        update={
            "whatsapp_backend": "disabled",
            "agent_backend": "disabled",
            "bumpa_backend": "disabled",
        }
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    @contextmanager
    def transaction():
        yield db
        db.commit()

    monkeypatch.setattr("app.db.session.SessionLocal", SimpleNamespace(begin=transaction))
    pilot_cli()
    result = json.loads(capsys.readouterr().out)
    assert result["armed"] == 1 and result["messages_sent"] == result["enrollments_created"] == 0
    assert len(db.scalars(select(PhoneIdentity)).all()) == 1


def test_greeting_during_active_setup_never_becomes_a_business_fact(business: Any) -> None:
    db, tenant, user = business
    row = arm(db, tenant, user)
    reply(db, tenant, user, row, "hi")
    before = deepcopy(state_for(db, tenant))
    assert "Question 1 of 7" in str(reply(db, tenant, user, row, "hello"))
    assert state_for(db, tenant) == before
