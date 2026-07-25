import base64
import binascii
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant
from app.core.rate_limit import enforce_operation_rate_limit
from app.db.models import AgentMessage, Conversation, GeneratedAgentMedia, PendingAgentAction
from app.db.session import get_db
from app.providers.redaction import redact_text
from app.schemas import (
    AgentActionDecision,
    ChatMessagePage,
    ChatMessageView,
    ChatRequest,
    ChatResponse,
    ConversationSummary,
    ConversationSummaryPage,
    GeneratedMediaView,
    PendingAgentActionView,
)
from app.services.agent_actions import (
    AgentActionError,
    confirmation_token,
    decide_pending_action,
)
from app.services.chat import data_freshness_at_message, handle_chat
from app.services.generated_media import GeneratedMediaError, generated_media_path

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
        generated_media=_generated_media_views(
            db,
            tenant_id=principal.tenant.id,
            user_id=principal.user.id,
            conversation_id=conversation.id,
            channel="web",
        ),
    )


@router.post("/web/stream")
def web_chat_stream(
    payload: ChatRequest,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream durable assistant and tool lifecycle events as SSE."""

    if settings.agent_backend == "disabled":
        raise HTTPException(status_code=503, detail="Hermes agent service is not configured")
    assert principal.tenant is not None
    tenant = principal.tenant
    cached: tuple[AgentMessage, AgentMessage] | None = None
    if payload.client_message_id:
        existing = db.scalar(
            select(AgentMessage).where(
                AgentMessage.tenant_id == tenant.id,
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
                cached = (existing, outbound)
    if cached is None:
        enforce_operation_rate_limit(
            settings,
            operation="chat",
            scopes={"tenant": tenant.id, "user": principal.user.id},
            limit=settings.chat_rate_limit,
            window_seconds=settings.chat_rate_limit_window_seconds,
        )

    def generate() -> Iterator[str]:
        yield _sse(
            "message.started",
            {
                "client_message_id": payload.client_message_id,
                "conversation_id": payload.conversation_id,
            },
        )
        if cached is not None:
            incoming, outgoing = cached
            for delta in _text_deltas(outgoing.content):
                yield _sse("message.delta", {"delta": delta})
            yield _sse(
                "message.completed",
                {
                    "conversation_id": incoming.conversation_id,
                    "inbound_message_id": incoming.id,
                    "outbound_message_id": outgoing.id,
                    "answer": outgoing.content,
                    "generated_media": [
                        item.model_dump()
                        for item in _generated_media_views(
                            db,
                            tenant_id=tenant.id,
                            user_id=principal.user.id,
                            conversation_id=incoming.conversation_id,
                            channel="web",
                        )
                    ],
                    "replayed": True,
                },
            )
            return

        events: Queue[tuple[str, dict[str, object]]] = Queue()
        streamed_text: list[str] = []

        def on_event(name: str, data: dict[str, object]) -> None:
            if name == "message.delta" and isinstance(data.get("delta"), str):
                streamed_text.append(str(data["delta"]))
            events.put((name, data))

        def run_agent() -> None:
            try:
                conversation, inbound, outbound, freshness = handle_chat(
                    db,
                    tenant=tenant,
                    user=principal.user,
                    message=payload.message,
                    channel="web",
                    conversation_id=payload.conversation_id,
                    external_message_id=payload.client_message_id,
                    event_callback=on_event,
                    settings=settings,
                )
                if not streamed_text:
                    for delta in _text_deltas(outbound.content):
                        events.put(("message.delta", {"delta": delta}))
                actions = db.scalars(
                    select(PendingAgentAction).where(
                        PendingAgentAction.tenant_id == tenant.id,
                        PendingAgentAction.user_id == principal.user.id,
                        PendingAgentAction.conversation_id == conversation.id,
                        PendingAgentAction.status == "pending",
                    )
                ).all()
                for action in actions:
                    events.put(
                        (
                            "confirmation.required",
                            {
                                "action_id": action.id,
                                "target": action.target_summary,
                                "exact_input": action.action_input,
                                "expires_at": action.expires_at.isoformat(),
                                "confirmation_token": confirmation_token(settings, action),
                            },
                        )
                    )
                events.put(
                    (
                        "message.completed",
                        {
                            "conversation_id": conversation.id,
                            "inbound_message_id": inbound.id,
                            "outbound_message_id": outbound.id,
                            "answer": outbound.content,
                            "data_freshness": (
                                freshness.isoformat() if isinstance(freshness, datetime) else None
                            ),
                            "generated_media": [
                                item.model_dump()
                                for item in _generated_media_views(
                                    db,
                                    tenant_id=tenant.id,
                                    user_id=principal.user.id,
                                    conversation_id=conversation.id,
                                    channel="web",
                                )
                            ],
                        },
                    )
                )
            except HTTPException as exc:
                events.put(
                    (
                        "error",
                        {
                            "code": "agent_unavailable",
                            "message": str(exc.detail)[:300],
                            "retryable": exc.status_code >= 500,
                        },
                    )
                )
            except Exception:
                db.rollback()
                events.put(
                    (
                        "error",
                        {
                            "code": "internal_error",
                            "message": "The assistant could not complete this request.",
                            "retryable": True,
                        },
                    )
                )

        worker = Thread(target=run_agent, name="chat-stream-agent", daemon=True)
        worker.start()
        terminal = False
        while not terminal:
            try:
                name, data = events.get(timeout=15)
            except Empty:
                if not worker.is_alive():
                    yield _sse(
                        "error",
                        {
                            "code": "stream_ended",
                            "message": "The assistant stream ended unexpectedly.",
                            "retryable": True,
                        },
                    )
                    break
                yield ": keep-alive\n\n"
                continue
            yield _sse(name, data)
            terminal = name in {"message.completed", "error"}

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/media/{media_id}")
def generated_media(
    media_id: str,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    assert principal.tenant is not None
    media = db.scalar(
        select(GeneratedAgentMedia).where(
            GeneratedAgentMedia.id == media_id,
            GeneratedAgentMedia.tenant_id == principal.tenant.id,
            GeneratedAgentMedia.user_id == principal.user.id,
            GeneratedAgentMedia.channel == "web",
            GeneratedAgentMedia.status == "pending",
        )
    )
    if media is None:
        raise HTTPException(status_code=404, detail="Generated media not found")
    try:
        content = generated_media_path(settings, media).read_bytes()
    except (GeneratedMediaError, OSError):
        raise HTTPException(status_code=404, detail="Generated media not found") from None
    if (
        len(content) != media.byte_size
        or hashlib.sha256(content).hexdigest() != media.checksum_sha256
    ):
        raise HTTPException(status_code=404, detail="Generated media not found")
    return Response(
        content=content,
        media_type=media.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="bumpa-bestie-{media.id}.png"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/actions", response_model=list[PendingAgentActionView])
def pending_actions(
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    status: str = Query(default="pending", pattern="^(pending|succeeded|failed|denied|expired)$"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PendingAgentActionView]:
    assert principal.tenant is not None
    rows = db.scalars(
        select(PendingAgentAction)
        .where(
            PendingAgentAction.tenant_id == principal.tenant.id,
            PendingAgentAction.user_id == principal.user.id,
            PendingAgentAction.status == status,
        )
        .order_by(PendingAgentAction.created_at.desc(), PendingAgentAction.id.desc())
        .limit(limit)
    ).all()
    return [_pending_action_view(row, settings) for row in rows]


@router.post(
    "/actions/{action_id}/confirm",
    response_model=PendingAgentActionView,
)
def confirm_pending_action(
    action_id: str,
    payload: AgentActionDecision,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PendingAgentActionView:
    assert principal.tenant is not None
    try:
        action = decide_pending_action(
            db,
            settings,
            action_id=action_id,
            tenant_id=principal.tenant.id,
            user_id=principal.user.id,
            supplied_token=payload.confirmation_token,
            decision="confirm",
        )
    except AgentActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _pending_action_view(action, settings)


@router.post(
    "/actions/{action_id}/deny",
    response_model=PendingAgentActionView,
)
def deny_pending_action(
    action_id: str,
    payload: AgentActionDecision,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PendingAgentActionView:
    assert principal.tenant is not None
    try:
        action = decide_pending_action(
            db,
            settings,
            action_id=action_id,
            tenant_id=principal.tenant.id,
            user_id=principal.user.id,
            supplied_token=payload.confirmation_token,
            decision="deny",
        )
    except AgentActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _pending_action_view(action, settings)


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


def _pending_action_view(
    action: PendingAgentAction,
    settings: Settings,
) -> PendingAgentActionView:
    return PendingAgentActionView(
        id=action.id,
        conversation_id=action.conversation_id,
        tool_name=action.tool_name,
        target_summary=action.target_summary,
        exact_input=action.action_input,
        status=action.status,
        expires_at=action.expires_at,
        confirmation_token=(
            confirmation_token(settings, action) if action.status == "pending" else None
        ),
        action_result=action.action_result,
    )


def _generated_media_views(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    channel: str,
) -> list[GeneratedMediaView]:
    rows = db.scalars(
        select(GeneratedAgentMedia)
        .where(
            GeneratedAgentMedia.tenant_id == tenant_id,
            GeneratedAgentMedia.user_id == user_id,
            GeneratedAgentMedia.conversation_id == conversation_id,
            GeneratedAgentMedia.channel == channel,
            GeneratedAgentMedia.status == "pending",
        )
        .order_by(GeneratedAgentMedia.created_at)
    ).all()
    return [
        GeneratedMediaView(
            id=row.id,
            media_type="image",
            mime_type=row.mime_type,
            byte_size=row.byte_size,
            url=f"/v1/chat/media/{row.id}",
        )
        for row in rows
    ]


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _text_deltas(value: str, size: int = 240) -> list[str]:
    return [value[index : index + size] for index in range(0, len(value), size)]


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
