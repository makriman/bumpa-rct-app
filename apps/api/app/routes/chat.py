from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant
from app.db.models import AgentMessage, Conversation
from app.db.session import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.chat import handle_chat

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/web", response_model=ChatResponse)
def web_chat(
    payload: ChatRequest,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    if settings.agent_backend != "mock":
        raise HTTPException(
            status_code=503,
            detail="Hermes agent integration is not configured yet",
        )
    assert principal.tenant is not None
    if payload.client_message_id:
        existing = db.scalar(
            select(AgentMessage).where(
                AgentMessage.tenant_id == principal.tenant.id,
                AgentMessage.channel == "web",
                AgentMessage.external_message_id == payload.client_message_id,
            )
        )
        if existing:
            outbound = db.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.conversation_id == existing.conversation_id,
                    AgentMessage.direction == "outbound",
                    AgentMessage.created_at >= existing.created_at,
                )
                .order_by(AgentMessage.created_at)
            )
            if outbound:
                return ChatResponse(
                    conversation_id=existing.conversation_id,
                    inbound_message_id=existing.id,
                    outbound_message_id=outbound.id,
                    answer=outbound.content,
                )
    conversation, inbound, outbound, freshness = handle_chat(
        db,
        tenant=principal.tenant,
        user=principal.user,
        message=payload.message,
        channel="web",
        conversation_id=payload.conversation_id,
        external_message_id=payload.client_message_id,
    )
    return ChatResponse(
        conversation_id=conversation.id,
        inbound_message_id=inbound.id,
        outbound_message_id=outbound.id,
        answer=outbound.content,
        data_freshness=freshness,
    )


@router.get("/conversations")
def conversations(
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict]:
    assert principal.tenant is not None
    rows = db.scalars(
        select(Conversation)
        .where(
            Conversation.tenant_id == principal.tenant.id,
            Conversation.user_id == principal.user.id,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "channel": row.channel,
            "title": row.title,
            "status": row.status,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.get("/conversations/{conversation_id}")
def conversation_messages(
    conversation_id: str,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
) -> dict:
    assert principal.tenant is not None
    conversation = db.get(Conversation, conversation_id)
    if (
        not conversation
        or conversation.tenant_id != principal.tenant.id
        or conversation.user_id != principal.user.id
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conversation.id)
        .order_by(AgentMessage.created_at)
    ).all()
    return {
        "id": conversation.id,
        "messages": [
            {
                "id": item.id,
                "direction": item.direction,
                "content": item.content,
                "created_at": item.created_at,
            }
            for item in messages
        ],
    }
