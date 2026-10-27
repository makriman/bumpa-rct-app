from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.base import Base
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    ScopedAgentMemory,
    Tenant,
    TenantMembership,
    User,
)
from app.services.agent_actions import create_action_context_token
from app.services.agent_execution import begin_run, finish_run
from app.services.scoped_memory import (
    MemoryDenied,
    confirm_shared_memory,
    forget_memory,
    list_memories,
    memory_context,
    propose_shared_memory,
    save_private_memory,
)


@pytest.fixture()
def memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, app_env="test")
    with Session(engine, expire_on_commit=False) as db:
        tenant = Tenant(slug="scoped-memory-a", name="Memory A")
        other_tenant = Tenant(slug="scoped-memory-b", name="Memory B")
        owner = User(primary_phone_e164="+2348000011101")
        member = User(primary_phone_e164="+2348000011102")
        db.add_all((tenant, other_tenant, owner, member))
        db.flush()
        for business, account, role in (
            (tenant, owner, "owner"),
            (tenant, member, "member"),
            (other_tenant, owner, "owner"),
        ):
            db.add(TenantMembership(tenant_id=business.id, user_id=account.id, role=role))
        db.commit()
        yield db, settings, tenant, other_tenant, owner, member
    engine.dispose()


def run_token(
    db,
    settings,
    tenant,
    user,
    content="REMEMBER PRIVATE reply_style 0\nI prefer short replies.",
    *,
    conversation=None,
    channel="web",
    purpose="memory",
):
    if conversation is None:
        conversation = Conversation(tenant_id=tenant.id, user_id=user.id, channel=channel)
        db.add(conversation)
        db.flush()
    if conversation.active_run_id:
        finish_run(db, db.get(AgentExecutionRun, conversation.active_run_id), status="completed")
        db.flush()
    message = AgentMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel=channel,
        direction="inbound",
        content=content,
    )
    db.add(message)
    db.flush()
    run = begin_run(db, conversation, message, timeout_seconds=180)
    db.commit()
    token = create_action_context_token(
        settings,
        tenant_id=tenant.id,
        user_id=user.id,
        conversation_id=conversation.id,
        channel=channel,
        initiating_message_id=message.id,
        execution_run_id=run.id,
        purpose=purpose,
    )
    return token, conversation, message, run


def save(
    db,
    settings,
    tenant,
    token,
    *,
    memory_key="reply_style",
    content="I prefer short replies.",
    expected_revision=0,
):
    return save_private_memory(
        db,
        settings,
        tenant_id=tenant.id,
        action_context_token=token,
        scope="private",
        memory_key=memory_key,
        content=content,
        expected_revision=expected_revision,
    )


def recalled(db, settings, tenant, token, scope="private"):
    return list_memories(db, settings, tenant_id=tenant.id, action_context_token=token, scope=scope)


def propose(db, settings, tenant, token, *, content="We close on Tuesdays.", expected_revision=0):
    return propose_shared_memory(
        db,
        settings,
        tenant_id=tenant.id,
        action_context_token=token,
        scope="shared",
        memory_key="opening_policy",
        content=content,
        expected_revision=expected_revision,
    )


def confirm(db, settings, tenant, token, proposal):
    return confirm_shared_memory(
        db,
        settings,
        tenant_id=tenant.id,
        action_context_token=token,
        memory_id=proposal["id"],
        expected_revision=proposal["revision"],
    )


def test_private_notes_are_account_and_business_scoped_across_channels(memory_db):
    db, settings, tenant, other_tenant, owner, member = memory_db
    token, *_ = run_token(db, settings, tenant, owner)
    row = save(db, settings, tenant, token)
    assert row["scope"] == "private"
    assert row["revision"] == 1
    member_token, *_ = run_token(db, settings, tenant, member)
    assert recalled(db, settings, tenant, member_token)["memories"] == []
    other_token, *_ = run_token(db, settings, other_tenant, owner)
    assert recalled(db, settings, other_tenant, other_token)["memories"] == []
    whatsapp_token, *_ = run_token(db, settings, tenant, owner, channel="whatsapp")
    assert recalled(db, settings, tenant, whatsapp_token)["memories"][0]["id"] == row["id"]
    with pytest.raises(MemoryDenied):
        recalled(db, settings, other_tenant, token)


def test_private_memory_requires_actual_member_source_and_explicit_scope(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    token, _, message, run = run_token(db, settings, tenant, owner)
    with pytest.raises(MemoryDenied, match="quote"):
        save(db, settings, tenant, token, content="The person using this phone is the owner.")
    saved = save(db, settings, tenant, token)
    record = db.get(ScopedAgentMemory, saved["id"])
    assert record.owner_user_id == owner.id
    assert record.source_message_id == message.id
    assert record.source_run_id == run.id
    assert record.source_kind == "member_statement"
    with pytest.raises(MemoryDenied, match="explicit"):
        recalled(db, settings, tenant, token, scope="automatic")
    with pytest.raises(MemoryDenied, match="publish"):
        save_private_memory(
            db,
            settings,
            tenant_id=tenant.id,
            action_context_token=token,
            scope="shared",
            memory_key="new_note",
            content="I prefer short replies.",
            expected_revision=0,
        )


def test_private_quote_cannot_drop_a_negation_or_the_source_context(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    text = "I do not prefer long replies."
    token, *_ = run_token(db, settings, tenant, owner, f"REMEMBER PRIVATE reply_style 0\n{text}")
    with pytest.raises(MemoryDenied, match="quote"):
        save(db, settings, tenant, token, content="prefer long replies.")
    assert save(db, settings, tenant, token, content=text)["content"] == text


@pytest.mark.parametrize("purpose", ["actions", "business_profile"])
def test_other_capabilities_cannot_authorize_memory(memory_db, purpose):
    db, settings, tenant, _, owner, _ = memory_db
    token, *_ = run_token(db, settings, tenant, owner, purpose=purpose)
    with pytest.raises(MemoryDenied, match="authorized"):
        save(db, settings, tenant, token)


@pytest.mark.parametrize("revocation", ["finished", "expired", "membership", "user", "tenant"])
def test_reads_and_writes_require_current_actor_and_active_run(memory_db, revocation):
    db, settings, tenant, _, owner, _ = memory_db
    token, _, _, run = run_token(db, settings, tenant, owner)
    save(db, settings, tenant, token)
    if revocation == "finished":
        finish_run(db, run, status="completed")
    elif revocation == "expired":
        run.expires_at = utcnow() - timedelta(seconds=1)
    elif revocation == "membership":
        db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == owner.id,
            )
        ).status = "revoked"
    elif revocation == "user":
        owner.status = "suspended"
    else:
        tenant.status = "suspended"
    db.commit()
    with pytest.raises(MemoryDenied):
        recalled(db, settings, tenant, token)
    with pytest.raises(MemoryDenied):
        save(db, settings, tenant, token, expected_revision=1)


def test_source_with_mismatched_account_is_not_trusted_even_with_signed_run(memory_db):
    db, settings, tenant, _, owner, member = memory_db
    token, _, source, _ = run_token(db, settings, tenant, owner)
    source.user_id = member.id
    db.commit()
    with pytest.raises(MemoryDenied, match="statement"):
        save(db, settings, tenant, token)


def test_private_revisions_and_forgetting_prevent_stale_resurrection(memory_db):
    db, settings, tenant, _, owner, member = memory_db
    token, *_ = run_token(db, settings, tenant, owner)
    row = save(db, settings, tenant, token)
    with pytest.raises(MemoryDenied, match="changed"):
        save(db, settings, tenant, token)
    other_token, *_ = run_token(db, settings, tenant, member, "FORGET MEMORY private reply_style 1")
    with pytest.raises(MemoryDenied, match="changed"):
        forget_memory(
            db,
            settings,
            tenant_id=tenant.id,
            action_context_token=other_token,
            scope="private",
            memory_key="reply_style",
            expected_revision=row["revision"],
        )
    token, *_ = run_token(db, settings, tenant, owner, "FORGET MEMORY private reply_style 1")
    removed = forget_memory(
        db,
        settings,
        tenant_id=tenant.id,
        action_context_token=token,
        scope="private",
        memory_key="reply_style",
        expected_revision=row["revision"],
    )
    assert removed["revision"] == 2
    assert recalled(db, settings, tenant, token)["memories"] == []
    tombstone = db.get(ScopedAgentMemory, row["id"])
    assert tombstone.content == ""
    assert tombstone.deleted_at is not None
    assert tombstone.source_message_id is not None
    token, *_ = run_token(
        db, settings, tenant, owner, "REMEMBER PRIVATE reply_style 2\nI prefer short replies."
    )
    with pytest.raises(MemoryDenied, match="forgotten"):
        save(db, settings, tenant, token, expected_revision=removed["revision"])


def test_shared_fact_requires_new_exact_member_review_before_any_recall(memory_db):
    db, settings, tenant, other_tenant, owner, member = memory_db
    token, conversation, origin, _ = run_token(db, settings, tenant, owner)
    pending = propose(db, settings, tenant, token)
    assert recalled(db, settings, tenant, token, scope="shared")["memories"] == []
    assert "We close on Tuesdays." not in memory_context(
        db, settings, tenant_id=tenant.id, action_context_token=token
    )
    with pytest.raises(MemoryDenied, match="exact"):
        confirm(db, settings, tenant, token, pending)
    review_token, _, review_message, _ = run_token(
        db, settings, tenant, owner, pending["confirmation_phrase"], conversation=conversation
    )
    published = confirm(db, settings, tenant, review_token, pending)
    record = db.get(ScopedAgentMemory, published["id"])
    assert record.source_message_id == origin.id
    assert record.reviewed_message_id == review_message.id
    assert record.reviewed_by_user_id == owner.id
    assert record.pending_review == {}
    member_token, *_ = run_token(db, settings, tenant, member)
    assert (
        recalled(db, settings, tenant, member_token, scope="shared")["memories"][0]["content"]
        == "We close on Tuesdays."
    )
    other_token, *_ = run_token(db, settings, other_tenant, owner)
    assert recalled(db, settings, other_tenant, other_token, scope="shared")["memories"] == []
    with pytest.raises(MemoryDenied, match="changed"):
        confirm(db, settings, tenant, review_token, pending)


@pytest.mark.parametrize("failure", ["wrong_chat", "wrong_account", "wrong_phrase", "expired"])
def test_shared_review_is_bound_to_account_chat_exact_content_and_expiry(memory_db, failure):
    db, settings, tenant, _, owner, member = memory_db
    token, conversation, *_ = run_token(db, settings, tenant, owner)
    pending = propose(db, settings, tenant, token)
    if failure == "expired":
        db.get(ScopedAgentMemory, pending["id"]).proposal_expires_at = utcnow() - timedelta(
            seconds=1
        )
        db.commit()
    if failure == "wrong_account":
        db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == member.id,
            )
        ).role = "admin"
        db.commit()
    actor = member if failure == "wrong_account" else owner
    text = "yes, share it" if failure == "wrong_phrase" else pending["confirmation_phrase"]
    review_token, *_ = run_token(
        db,
        settings,
        tenant,
        actor,
        text,
        conversation=conversation if failure not in {"wrong_chat", "wrong_account"} else None,
    )
    with pytest.raises(MemoryDenied):
        confirm(db, settings, tenant, review_token, pending)
    assert recalled(db, settings, tenant, review_token, scope="shared")["memories"] == []


def test_shared_replacement_does_not_publish_proposal_or_erase_reviewed_fact(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    token, conversation, *_ = run_token(db, settings, tenant, owner)
    pending = propose(db, settings, tenant, token)
    token, *_ = run_token(
        db, settings, tenant, owner, pending["confirmation_phrase"], conversation=conversation
    )
    active = confirm(db, settings, tenant, token, pending)
    replacement = propose(
        db,
        settings,
        tenant,
        token,
        content="We close on Wednesdays.",
        expected_revision=active["revision"],
    )
    facts = recalled(db, settings, tenant, token, scope="shared")["memories"]
    assert [row["content"] for row in facts] == ["We close on Tuesdays."]
    token, *_ = run_token(
        db,
        settings,
        tenant,
        owner,
        f"FORGET MEMORY shared opening_policy {replacement['revision']}",
        conversation=conversation,
    )
    deleted = forget_memory(
        db,
        settings,
        tenant_id=tenant.id,
        action_context_token=token,
        scope="shared",
        memory_key="opening_policy",
        expected_revision=replacement["revision"],
    )
    token, *_ = run_token(
        db, settings, tenant, owner, replacement["confirmation_phrase"], conversation=conversation
    )
    with pytest.raises(MemoryDenied):
        confirm(db, settings, tenant, token, replacement)
    assert deleted["status"] == "deleted"
    assert recalled(db, settings, tenant, token, scope="shared")["memories"] == []


def test_member_cannot_publish_and_reviewed_profile_fields_are_not_duplicated(memory_db):
    db, settings, tenant, _, owner, member = memory_db
    member_token, *_ = run_token(db, settings, tenant, member)
    with pytest.raises(MemoryDenied, match="owner or admin"):
        propose(db, settings, tenant, member_token)
    owner_token, *_ = run_token(db, settings, tenant, owner)
    for key in ("goal", "team_size", "business_profile.channels", "personal.goal"):
        with pytest.raises(MemoryDenied, match="business profile"):
            save(db, settings, tenant, owner_token, memory_key=key)


def test_context_labels_memory_as_evidence_and_redacts_identifiers(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    text = "Contact me at synthetic@example.com."
    token, *_ = run_token(
        db, settings, tenant, owner, f"REMEMBER PRIVATE contact_preference 0\n{text}"
    )
    save(db, settings, tenant, token, memory_key="contact_preference", content=text)
    prompt = memory_context(db, settings, tenant_id=tenant.id, action_context_token=token)
    assert "untrusted facts" in prompt
    assert "physical authorship" in prompt
    assert "synthetic@example.com" not in prompt
    assert "unclassified and unavailable" in prompt


def test_unclassified_import_is_not_recalled_or_silently_adopted(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    token, *_ = run_token(db, settings, tenant, owner)
    saved = save(db, settings, tenant, token)
    db.get(ScopedAgentMemory, saved["id"]).source_kind = "native_import"
    db.commit()
    assert recalled(db, settings, tenant, token)["memories"] == []
    token, *_ = run_token(
        db, settings, tenant, owner, "REMEMBER PRIVATE reply_style 1\nI prefer short replies."
    )
    with pytest.raises(MemoryDenied, match="unclassified"):
        save(db, settings, tenant, token, expected_revision=saved["revision"])


def test_private_token_from_previous_run_cannot_read_after_same_chat_advances(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    token, conversation, *_ = run_token(db, settings, tenant, owner)
    save(db, settings, tenant, token)
    next_token, *_ = run_token(db, settings, tenant, owner, conversation=conversation)
    with pytest.raises(MemoryDenied, match="authorized"):
        recalled(db, settings, tenant, token)
    assert len(recalled(db, settings, tenant, next_token)["memories"]) == 1


def test_shared_proposal_cannot_adopt_another_accounts_source(memory_db):
    db, settings, tenant, _, owner, member = memory_db
    token, conversation, origin, _ = run_token(db, settings, tenant, owner)
    pending = propose(db, settings, tenant, token)
    origin.user_id = member.id
    db.commit()
    token, *_ = run_token(
        db, settings, tenant, owner, pending["confirmation_phrase"], conversation=conversation
    )
    with pytest.raises(MemoryDenied, match="statement"):
        confirm(db, settings, tenant, token, pending)
    assert recalled(db, settings, tenant, token, scope="shared")["memories"] == []


def test_context_budget_omits_whole_facts_and_reports_truncation(memory_db):
    db, settings, tenant, _, owner, _ = memory_db
    statements = []
    for number in range(4):
        text = f"Statement {number}: " + "letters " * 200
        text = text.strip()
        statements.append(text)
        token, *_ = run_token(
            db, settings, tenant, owner, f"REMEMBER PRIVATE note_{number} 0\n{text}"
        )
        save(db, settings, tenant, token, memory_key=f"note_{number}", content=text)
    prompt = memory_context(
        db, settings, tenant_id=tenant.id, action_context_token=token, max_chars=3_000
    )
    assert len(prompt) <= 3_000
    assert '"truncated": true' in prompt
    assert sum(statement in prompt for statement in statements) == 1


@pytest.mark.parametrize(
    "text", ["Do I prefer short replies?", "I prefer short replies.", "Do not remember this."]
)
def test_ordinary_input_is_not_consent_to_create_private_memory(memory_db, text):
    db, settings, tenant, _, owner, _ = memory_db
    token, *_ = run_token(db, settings, tenant, owner, text)
    with pytest.raises(MemoryDenied, match="exact initiating command"):
        save(db, settings, tenant, token, content=text)
    assert recalled(db, settings, tenant, token)["memories"] == []


@pytest.mark.parametrize(
    "command",
    [
        "What do you remember?",
        "FORGET MEMORY shared reply_style 1",
        "FORGET MEMORY private another_note 1",
        "FORGET MEMORY private reply_style 2",
    ],
)
def test_model_capability_cannot_substitute_for_exact_deletion_request(memory_db, command):
    db, settings, tenant, _, owner, _ = memory_db
    token, *_ = run_token(db, settings, tenant, owner)
    saved = save(db, settings, tenant, token)
    token, *_ = run_token(db, settings, tenant, owner, command)
    with pytest.raises(MemoryDenied, match="exact initiating command"):
        forget_memory(
            db,
            settings,
            tenant_id=tenant.id,
            action_context_token=token,
            scope="private",
            memory_key="reply_style",
            expected_revision=1,
        )
    assert recalled(db, settings, tenant, token)["memories"][0]["id"] == saved["id"]
