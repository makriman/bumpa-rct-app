from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.ids import new_id
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    Conversation,
    GeneratedAgentMedia,
    PendingAgentAction,
    TenantMembership,
)
from app.db.session import SessionLocal
from app.services.agent_actions import confirmation_token
from tests.conftest import auth_headers


def _owner_context() -> tuple[str, str]:
    with SessionLocal() as db:
        membership = db.scalar(select(TenantMembership).where(TenantMembership.role == "owner"))
        assert membership is not None
        return membership.tenant_id, membership.user_id


def test_pending_action_routes_bind_user_preview_token_and_single_use(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "+2348012345678")
    tenant_id, user_id = _owner_context()
    settings = get_settings()
    with SessionLocal() as db:
        conversation = Conversation(tenant_id=tenant_id, user_id=user_id, channel="web")
        db.add(conversation)
        db.flush()
        action = PendingAgentAction(
            id=new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation.id,
            tool_name="send_message",
            target_summary="Send this exact quotation to the supplier.",
            action_input={"to": "supplier@example.com", "body": "NGN 25,000"},
            status="pending",
            idempotency_key="chat-action-route",
            confirmation_token_hash="0" * 64,
            expires_at=utcnow() + timedelta(minutes=10),
        )
        token = confirmation_token(settings, action)
        action.confirmation_token_hash = hashlib.sha256(token.encode()).hexdigest()
        db.add(action)
        db.commit()
        action_id = action.id

    listed = client.get("/v1/chat/actions", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["exact_input"]["body"] == "NGN 25,000"
    assert listed.json()[0]["confirmation_token"] == token

    invalid = client.post(
        f"/v1/chat/actions/{action_id}/confirm",
        headers=headers,
        json={"confirmation_token": "x" * 64},
    )
    assert invalid.status_code == 409
    denied = client.post(
        f"/v1/chat/actions/{action_id}/deny",
        headers=headers,
        json={"confirmation_token": token},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"
    assert denied.json()["confirmation_token"] is None
    replay = client.post(
        f"/v1/chat/actions/{action_id}/deny",
        headers=headers,
        json={"confirmation_token": token},
    )
    assert replay.status_code == 409


def test_generated_media_download_is_integrity_checked_and_tenant_bound(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "+2348012345678")
    tenant_id, user_id = _owner_context()
    settings = get_settings()
    content = b"\x89PNG\r\n\x1a\nweb-generated-fixture"
    media_id = new_id()
    relative = Path("generated-media") / "test" / f"{media_id}.png"
    path = settings.artifact_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with SessionLocal() as db:
        conversation = Conversation(tenant_id=tenant_id, user_id=user_id, channel="web")
        db.add(conversation)
        db.flush()
        outbound = AgentMessage(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation.id,
            channel="web",
            direction="outbound",
            content="Your inventory export is ready.",
        )
        db.add(outbound)
        db.flush()
        db.add(
            GeneratedAgentMedia(
                id=media_id,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation.id,
                agent_message_id=outbound.id,
                channel="web",
                media_type="image",
                mime_type="image/png",
                filename="inventory-plan.png",
                storage_path=relative.as_posix(),
                byte_size=len(content),
                checksum_sha256=hashlib.sha256(content).hexdigest(),
                status="pending",
            )
        )
        db.commit()
        conversation_id = conversation.id

    response = client.get(f"/v1/chat/media/{media_id}", headers=headers)
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["cache-control"] == "private, no-store"
    assert 'filename="inventory-plan.png"' in response.headers["content-disposition"]
    page = client.get(
        f"/v1/chat/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert page.status_code == 200, page.text
    assert page.json()["items"][0]["generated_media"] == [
        {
            "id": media_id,
            "media_type": "image",
            "mime_type": "image/png",
            "filename": "inventory-plan.png",
            "byte_size": len(content),
            "url": f"/v1/chat/media/{media_id}",
        }
    ]
    legacy = client.get(
        f"/v1/chat/conversations/{conversation_id}",
        headers=headers,
    )
    assert legacy.status_code == 200
    assert legacy.json()["messages"][0]["generated_media"][0]["id"] == media_id
    path.write_bytes(b"tampered")
    rejected = client.get(f"/v1/chat/media/{media_id}", headers=headers)
    assert rejected.status_code == 404
    path.unlink(missing_ok=True)
