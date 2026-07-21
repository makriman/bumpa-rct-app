from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.core.time import utcnow
from app.db.models import AgentMessage, Conversation, PhoneIdentity, Tenant, TenantMembership, User
from app.db.session import SessionLocal, engine
from tests.conftest import auth_headers


def _seed_chat_history() -> tuple[str, list[str]]:
    phone = "+2348012300099"
    with SessionLocal() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "demo-store"))
        assert tenant is not None
        user = User(
            id="chat-pagination-user",
            name="Pagination Owner",
            primary_phone_e164=phone,
            email="pagination@example.test",
        )
        db.add(user)
        db.flush()
        db.add_all(
            [
                TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner"),
                PhoneIdentity(tenant_id=tenant.id, user_id=user.id, phone_e164=phone),
            ]
        )
        base_time = utcnow()
        conversation_ids: list[str] = []
        for index in range(5):
            conversation_id = f"chat-page-conversation-{index}"
            conversation_ids.append(conversation_id)
            updated_at = base_time if index < 2 else base_time - timedelta(minutes=index)
            conversation = Conversation(
                id=conversation_id,
                tenant_id=tenant.id,
                user_id=user.id,
                channel="web",
                status="open",
                title=f"Conversation {index}",
                created_at=updated_at,
                updated_at=updated_at,
            )
            db.add(conversation)
            db.flush()
            db.add_all(
                [
                    AgentMessage(
                        id=f"chat-page-inbound-{index}",
                        tenant_id=tenant.id,
                        user_id=user.id,
                        conversation_id=conversation.id,
                        channel="web",
                        direction="inbound",
                        content=f"Question {index}",
                        redacted_content=f"Question {index}",
                        external_message_id=f"chat-page-client-{index}",
                        created_at=updated_at - timedelta(seconds=1),
                    ),
                    AgentMessage(
                        id=f"chat-page-outbound-{index}",
                        tenant_id=tenant.id,
                        user_id=user.id,
                        conversation_id=conversation.id,
                        channel="web",
                        direction="outbound",
                        content=f"Email Ada at ada{index}@example.com or +2348012345678 for answer {index}",
                        redacted_content=None,
                        external_message_id=None,
                        created_at=updated_at,
                    ),
                ]
            )
        db.commit()
    return phone, conversation_ids


def _seed_conversations(
    *, phone: str, user_id: str, prefix: str, count: int
) -> tuple[str, list[str]]:
    with SessionLocal() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "demo-store"))
        assert tenant is not None
        db.add(
            User(
                id=user_id,
                name="Cursor Test Owner",
                primary_phone_e164=phone,
                email=f"{user_id}@example.test",
            )
        )
        db.flush()
        db.add_all(
            [
                TenantMembership(tenant_id=tenant.id, user_id=user_id, role="owner"),
                PhoneIdentity(tenant_id=tenant.id, user_id=user_id, phone_e164=phone),
            ]
        )
        base_time = utcnow()
        conversation_ids = []
        for index in range(count):
            conversation_id = f"{prefix}-{index:03d}"
            conversation_ids.append(conversation_id)
            db.add(
                Conversation(
                    id=conversation_id,
                    tenant_id=tenant.id,
                    user_id=user_id,
                    channel="web",
                    status="open",
                    title=f"History item {index}",
                    created_at=base_time - timedelta(seconds=index),
                    updated_at=base_time - timedelta(seconds=index),
                )
            )
        db.commit()
        return tenant.id, conversation_ids


def test_conversation_and_message_cursor_pagination_is_stable_and_isolated(
    client: TestClient,
) -> None:
    phone, conversation_ids = _seed_chat_history()
    owner = auth_headers(client, phone)

    first = client.get("/v1/chat/conversations/page?limit=2", headers=owner)
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert [item["id"] for item in first_page["items"]] == [
        "chat-page-conversation-1",
        "chat-page-conversation-0",
    ]
    assert first_page["next_cursor"]
    assert "[EMAIL]" in first_page["items"][0]["last_message_preview"]
    assert "[PHONE]" in first_page["items"][0]["last_message_preview"]
    assert "ada1@example.com" not in first_page["items"][0]["last_message_preview"]

    second = client.get(
        "/v1/chat/conversations/page",
        headers=owner,
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    second_ids = [item["id"] for item in second.json()["items"]]
    assert second_ids == ["chat-page-conversation-2", "chat-page-conversation-3"]
    assert not set(second_ids) & {item["id"] for item in first_page["items"]}

    messages = client.get(
        f"/v1/chat/conversations/{conversation_ids[0]}/messages?limit=1",
        headers=owner,
    )
    assert messages.status_code == 200, messages.text
    message_page = messages.json()
    assert [item["id"] for item in message_page["items"]] == ["chat-page-outbound-0"]
    assert message_page["next_cursor"]
    older = client.get(
        f"/v1/chat/conversations/{conversation_ids[0]}/messages",
        headers=owner,
        params={"limit": 1, "cursor": message_page["next_cursor"]},
    )
    assert [item["id"] for item in older.json()["items"]] == ["chat-page-inbound-0"]
    assert older.json()["next_cursor"] is None

    other_owner = auth_headers(client, "+2348012345679")
    isolated = client.get(
        f"/v1/chat/conversations/{conversation_ids[0]}/messages",
        headers=other_owner,
    )
    assert isolated.status_code == 404


def test_chat_pagination_rejects_malformed_cursors(client: TestClient) -> None:
    owner = auth_headers(client, "+2348012345678")
    conversations = client.get(
        "/v1/chat/conversations/page",
        headers=owner,
        params={"cursor": "not-a-valid-cursor"},
    )
    assert conversations.status_code == 400
    assert conversations.json()["detail"] == "Invalid pagination cursor"

    created = client.post(
        "/v1/chat/web",
        headers=owner,
        json={"message": "Show my sales", "client_message_id": "malformed-cursor-test"},
    )
    assert created.status_code == 200, created.text
    messages = client.get(
        f"/v1/chat/conversations/{created.json()['conversation_id']}/messages",
        headers=owner,
        params={"cursor": "not-a-valid-cursor"},
    )
    assert messages.status_code == 400


def test_pagination_handles_empty_and_large_histories_without_n_plus_one_queries(
    client: TestClient,
) -> None:
    _seed_conversations(
        phone="+2348012300198",
        user_id="chat-empty-user",
        prefix="chat-empty",
        count=0,
    )
    empty_owner = auth_headers(client, "+2348012300198")
    empty = client.get("/v1/chat/conversations/page", headers=empty_owner)
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "next_cursor": None}

    tenant_id, expected_ids = _seed_conversations(
        phone="+2348012300199",
        user_id="chat-large-user",
        prefix="chat-large",
        count=105,
    )
    large_owner = auth_headers(client, "+2348012300199")
    observed_statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        observed_statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        first = client.get(
            "/v1/chat/conversations/page", headers=large_owner, params={"limit": 100}
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert first.status_code == 200, first.text
    first_page = first.json()
    assert len(first_page["items"]) == 100
    assert first_page["next_cursor"]
    history_queries = [
        statement
        for statement in observed_statements
        if "conversations" in statement.lower() or "agent_messages" in statement.lower()
    ]
    assert len(history_queries) == 1

    second = client.get(
        "/v1/chat/conversations/page",
        headers=large_owner,
        params={"limit": 100, "cursor": first_page["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    assert len(second_page["items"]) == 5
    assert second_page["next_cursor"] is None
    returned_ids = [item["id"] for item in first_page["items"] + second_page["items"]]
    assert returned_ids == expected_ids

    with engine.connect() as connection:
        query_plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT id FROM conversations "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "ORDER BY updated_at DESC, id DESC LIMIT 30"
            ),
            {"tenant_id": tenant_id, "user_id": "chat-large-user"},
        ).all()
    assert "ix_conversation_tenant_user_updated_id" in " ".join(str(row[-1]) for row in query_plan)


def test_message_cursor_ties_boundaries_and_authentication_fail_closed(
    client: TestClient,
) -> None:
    tenant_id, conversation_ids = _seed_conversations(
        phone="+2348012300200",
        user_id="chat-tied-message-user",
        prefix="chat-tied-message",
        count=1,
    )
    conversation_id = conversation_ids[0]
    tied_at = utcnow()
    with SessionLocal() as db:
        db.add_all(
            [
                AgentMessage(
                    id=f"tied-message-{suffix}",
                    tenant_id=tenant_id,
                    user_id="chat-tied-message-user",
                    conversation_id=conversation_id,
                    channel="web",
                    direction="inbound" if suffix != "c" else "outbound",
                    content=f"Tied message {suffix}",
                    redacted_content=f"Tied message {suffix}",
                    created_at=tied_at,
                )
                for suffix in ("a", "b", "c")
            ]
        )
        db.commit()

    client.cookies.clear()
    assert client.get("/v1/chat/conversations/page").status_code == 401
    assert client.get(f"/v1/chat/conversations/{conversation_id}/messages").status_code == 401

    owner = auth_headers(client, "+2348012300200")
    assert (
        client.get("/v1/chat/conversations/page", headers=owner, params={"limit": 0}).status_code
        == 422
    )
    assert (
        client.get(
            f"/v1/chat/conversations/{conversation_id}/messages",
            headers=owner,
            params={"limit": 101},
        ).status_code
        == 422
    )

    newest = client.get(
        f"/v1/chat/conversations/{conversation_id}/messages",
        headers=owner,
        params={"limit": 2},
    )
    assert newest.status_code == 200, newest.text
    assert [item["id"] for item in newest.json()["items"]] == [
        "tied-message-b",
        "tied-message-c",
    ]
    assert newest.json()["next_cursor"]

    oldest = client.get(
        f"/v1/chat/conversations/{conversation_id}/messages",
        headers=owner,
        params={"limit": 2, "cursor": newest.json()["next_cursor"]},
    )
    assert oldest.status_code == 200, oldest.text
    assert [item["id"] for item in oldest.json()["items"]] == ["tied-message-a"]
    assert oldest.json()["next_cursor"] is None
