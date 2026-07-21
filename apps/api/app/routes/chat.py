import base64
import binascii
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant
from app.core.rate_limit import enforce_operation_rate_limit
from app.db.models import AgentMessage, Conversation
from app.db.session import get_db
from app.providers.redaction import redact_text
from app.schemas import (
    ChatMessagePage,
    ChatMessageView,
    ChatRequest,
    ChatResponse,
    ConversationSummary,
    ConversationSummaryPage,
)
from app.services.chat import data_freshness_at_message, handle_chat

router = APIRouter(prefix="/chat", tags=["chat"])

CURSOR_TIMESTAMP_KEY = "at"
CURSOR_ID_KEY = "id"


def _encode_cursor(timestamp: datetime, item_id: str) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    payload = json.dumps(
        {
            CURSOR_TIMESTAMP_KEY: timestamp.astimezone(UTC).isoformat(),
            CURSOR_ID_KEY: item_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
        timestamp = datetime.fromisoformat(payload[CURSOR_TIMESTAMP_KEY])
        item_id = payload[CURSOR_ID_KEY]
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        if not isinstance(item_id, str) or not item_id or len(item_id) > 128:
            raise ValueError("invalid cursor id")
        return timestamp, item_id
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor") from exc


def _message_preview(content: str | None) -> str | None:
    if not content:
        return None
    normalized = " ".join(redact_text(content).split())
    if not normalized:
        return None
    return normalized if len(normalized) <= 160 else f"{normalized[:159].rstrip()}…"


@router.post("/web", response_model=ChatResponse)
def web_chat(
    payload: ChatRequest,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    if settings.agent_backend == "disabled":
        raise HTTPException(
            status_code=503,
            detail="Hermes agent service is not configured",
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
                    data_freshness=data_freshness_at_message(
                        db,
                        tenant_id=principal.tenant.id,
                        message_created_at=existing.created_at,
                    ),
                )
    enforce_operation_rate_limit(
        settings,
        operation="chat",
        scopes={"tenant": principal.tenant.id, "user": principal.user.id},
        limit=settings.chat_rate_limit,
        window_seconds=settings.chat_rate_limit_window_seconds,
    )
    conversation, inbound, outbound, freshness = handle_chat(
        db,
        tenant=principal.tenant,
        user=principal.user,
        message=payload.message,
        channel="web",
        conversation_id=payload.conversation_id,
        external_message_id=payload.client_message_id,
        settings=settings,
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


@router.get("/conversations/page", response_model=ConversationSummaryPage)
def conversation_page(
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
) -> ConversationSummaryPage:
    assert principal.tenant is not None
    latest_message = (
        select(AgentMessage.content)
        .where(AgentMessage.conversation_id == Conversation.id)
        .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    statement = select(Conversation, latest_message.label("last_message")).where(
        Conversation.tenant_id == principal.tenant.id,
        Conversation.user_id == principal.user.id,
    )
    if cursor:
        cursor_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                Conversation.updated_at < cursor_at,
                and_(
                    Conversation.updated_at == cursor_at,
                    Conversation.id < cursor_id,
                ),
            )
        )
    rows = db.execute(
        statement.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [
        ConversationSummary(
            id=conversation.id,
            title=conversation.title,
            channel=conversation.channel,
            updated_at=conversation.updated_at,
            last_message_preview=_message_preview(last_message),
        )
        for conversation, last_message in page_rows
    ]
    next_cursor = None
    if has_more and page_rows:
        final_conversation = page_rows[-1][0]
        next_cursor = _encode_cursor(final_conversation.updated_at, final_conversation.id)
    return ConversationSummaryPage(items=items, next_cursor=next_cursor)


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


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessagePage,
)
def conversation_message_page(
    conversation_id: str,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> ChatMessagePage:
    assert principal.tenant is not None
    conversation = db.get(Conversation, conversation_id)
    if (
        not conversation
        or conversation.tenant_id != principal.tenant.id
        or conversation.user_id != principal.user.id
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")

    statement = select(AgentMessage).where(
        AgentMessage.tenant_id == principal.tenant.id,
        AgentMessage.conversation_id == conversation.id,
    )
    if cursor:
        cursor_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                AgentMessage.created_at < cursor_at,
                and_(AgentMessage.created_at == cursor_at, AgentMessage.id < cursor_id),
            )
        )
    rows = db.scalars(
        statement.order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [
        ChatMessageView(
            id=item.id,
            direction=item.direction,
            content=item.content,
            created_at=item.created_at,
        )
        for item in reversed(page_rows)
    ]
    next_cursor = None
    if has_more and page_rows:
        final_message = page_rows[-1]
        next_cursor = _encode_cursor(final_message.created_at, final_message.id)
    return ChatMessagePage(items=items, next_cursor=next_cursor)
