from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.ids import new_id
from app.core.time import utcnow
from app.db.models import (
    AgentExecutionRun,
    AgentMessage,
    Conversation,
    McpConnection,
    McpToolPermission,
    MerchantBusinessProfile,
    OperationalAgentEvent,
    PendingAgentAction,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
)
from app.services.agent_execution import ExecutionDenied, require_active_run
from app.services.connector_executor import (
    ConnectorExecutionError,
    execute_connector_write,
)
from app.services.mcp_permissions import McpPermissionDenied, authorize_mcp_tool

ACTION_CONTEXT_TTL_SECONDS = 7_200
ACTION_CONFIRMATION_TTL_SECONDS = 900


class AgentActionError(RuntimeError):
    """A safe action-control failure."""


@dataclass(frozen=True)
class ActionContext:
    tenant_id: str
    user_id: str
    conversation_id: str
    channel: str
    request_id: str
    initiating_message_id: str | None
    expires_at: int
    execution_run_id: str | None = None


def create_action_context_token(
    settings: Settings,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    channel: str,
    initiating_message_id: str | None = None,
    execution_run_id: str | None = None,
    purpose: Literal["actions", "business_profile", "memory"] = "actions",
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "channel": channel,
        "request_id": new_id(),
        "initiating_message_id": initiating_message_id,
        "execution_run_id": execution_run_id,
        "expires_at": int(utcnow().timestamp()) + ACTION_CONTEXT_TTL_SECONDS,
    }
    encoded = _encode_json(payload)
    domain = {
        "actions": "context",
        "business_profile": "business_profile_context",
        "memory": "memory_context",
    }[purpose]
    return f"{encoded}.{_signature(settings, domain, encoded)}"


def decode_action_context_token(
    settings: Settings,
    token: str,
    *,
    expected_tenant_id: str,
    db: Session | None = None,
    purpose: Literal["actions", "business_profile", "memory"] = "actions",
) -> ActionContext:
    try:
        encoded, supplied_signature = token.split(".", 1)
        domain = {
            "actions": "context",
            "business_profile": "business_profile_context",
            "memory": "memory_context",
        }[purpose]
        expected_signature = _signature(settings, domain, encoded)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature")
        payload = json.loads(_decode(encoded))
        context = ActionContext(
            tenant_id=str(payload["tenant_id"]),
            user_id=str(payload["user_id"]),
            conversation_id=str(payload["conversation_id"]),
            channel=str(payload["channel"]),
            request_id=str(
                payload.get("request_id") or hashlib.sha256(encoded.encode("ascii")).hexdigest()
            ),
            initiating_message_id=(
                str(payload["initiating_message_id"])
                if payload.get("initiating_message_id")
                else None
            ),
            expires_at=int(payload["expires_at"]),
            execution_run_id=payload.get("execution_run_id"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AgentActionError("The action context is invalid or expired.") from exc
    if (
        context.tenant_id != expected_tenant_id
        or context.expires_at < int(utcnow().timestamp())
        or context.channel not in {"web", "whatsapp"}
        or not context.user_id
        or not context.conversation_id
        or not context.request_id
    ):
        raise AgentActionError("The action context is invalid or expired.")
    if db is not None:
        try:
            require_active_run(
                db,
                run_id=context.execution_run_id,
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                initiating_message_id=context.initiating_message_id,
                channel=context.channel,
            )
        except ExecutionDenied as exc:
            raise AgentActionError("The initiating run is no longer active.") from exc
    return context


def require_write_actor(db: Session, tenant_id: str, user_id: str) -> None:
    member = db.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    tenant = db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not member
        or member.status != "active"
        or member.role not in {"owner", "admin"}
        or not tenant
        or tenant.status != "active"
        or not user
        or user.status != "active"
    ):
        raise AgentActionError("An active business owner or admin must authorize external writes.")


def authorization_revision(db: Session, tenant_id: str, connection_id: str, tool_name: str) -> str:
    connection = db.scalar(
        select(McpConnection)
        .where(McpConnection.id == connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    permission = db.scalar(
        select(McpToolPermission)
        .where(
            McpToolPermission.tenant_id == tenant_id,
            McpToolPermission.mcp_connection_id == connection_id,
            McpToolPermission.tool_name == tool_name,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    profile = db.scalar(
        select(MerchantBusinessProfile)
        .where(MerchantBusinessProfile.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if connection is None or connection.tenant_id != tenant_id or permission is None:
        raise AgentActionError("The connector authorization is no longer available.")
    data = {
        "connection": connection.id,
        "provider": connection.provider,
        "status": connection.status,
        "approved": connection.admin_approved,
        "read_only": connection.read_only,
        "resources": connection.allowed_resources,
        "credentials_hash": hashlib.sha256(
            (connection.encrypted_credentials or "").encode()
        ).hexdigest(),
        "permission": permission.permission,
        "profile_revision": (profile.state or {}).get("revision") if profile else None,
        "profile_state_hash": hashlib.sha256(
            json.dumps(profile.state if profile else None, sort_keys=True, default=str).encode()
        ).hexdigest(),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def action_binding(db: Session, context: ActionContext) -> dict[str, Any]:
    conversation = db.get(Conversation, context.conversation_id)
    run = db.get(AgentExecutionRun, context.execution_run_id)
    if conversation is None or run is None:
        raise AgentActionError("The initiating action is unavailable.")
    return {
        "conversation_id": conversation.id,
        "channel": context.channel,
        "lane": conversation.environment_lane,
        "waba": conversation.receiver_waba_id,
        "receiver": conversation.receiver_phone_id,
        "actor_identity_id": run.actor_identity_id,
        "initiating_message_id": context.initiating_message_id,
        "execution_run_id": run.id,
    }


def validate_approval_scope(
    db: Session,
    action: PendingAgentAction,
    *,
    conversation_id: str,
    channel: str,
    actor_identity_id: str | None,
) -> None:
    bound = action.approval_binding or {}
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not conversation
        or conversation.tenant_id != action.tenant_id
        or conversation.user_id != action.user_id
        or conversation.channel != channel
        or conversation.context_generation != 1
        or conversation.status not in {"open", "voice"}
        or bound.get("conversation_id") != conversation_id
        or bound.get("channel") != channel
        or bound.get("actor_identity_id") != actor_identity_id
        or bound.get("lane") != conversation.environment_lane
        or bound.get("waba") != conversation.receiver_waba_id
        or bound.get("receiver") != conversation.receiver_phone_id
    ):
        raise AgentActionError("This approval is not available in this receiving conversation.")
    if actor_identity_id is not None:
        identity = db.scalar(
            select(PhoneIdentity)
            .where(PhoneIdentity.id == actor_identity_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            not identity
            or identity.status != "approved"
            or identity.opt_out
            or identity.tenant_id != action.tenant_id
            or identity.user_id != action.user_id
        ):
            raise AgentActionError("The initiating phone identity is no longer authorized.")
    elif channel == "whatsapp":
        raise AgentActionError("The initiating phone identity is unavailable.")


def _validate_bound_source(db: Session, action: PendingAgentAction) -> None:
    bound = action.approval_binding or {}
    source_conversation = bound.get("conversation_id")
    channel = bound.get("channel")
    actor = bound.get("actor_identity_id")
    source_run = bound.get("execution_run_id")
    source_message = bound.get("initiating_message_id")
    if (
        not isinstance(source_conversation, str)
        or source_conversation != action.conversation_id
        or channel not in {"web", "whatsapp"}
        or (actor is not None and not isinstance(actor, str))
        or not isinstance(source_run, str)
        or not isinstance(source_message, str)
    ):
        raise AgentActionError(
            "The initiating action binding is unavailable; review a fresh preview."
        )
    validate_approval_scope(
        db, action, conversation_id=source_conversation, channel=channel, actor_identity_id=actor
    )
    run = db.scalar(
        select(AgentExecutionRun)
        .where(AgentExecutionRun.id == source_run)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    inbound = db.scalar(
        select(AgentMessage)
        .where(AgentMessage.id == source_message)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not run
        or not inbound
        or run.tenant_id != action.tenant_id
        or run.user_id != action.user_id
        or run.conversation_id != source_conversation
        or run.initiating_message_id != source_message
        or run.actor_identity_id != actor
        or inbound.tenant_id != action.tenant_id
        or inbound.user_id != action.user_id
        or inbound.conversation_id != source_conversation
        or inbound.channel != channel
        or inbound.direction != "inbound"
    ):
        raise AgentActionError("The initiating action source is no longer valid.")


def prepare_pending_action(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    connection_id: str,
    tool_name: str,
    target_summary: str,
    action_input: dict[str, Any],
    action_context_token: str,
) -> tuple[PendingAgentAction, str]:
    if not settings.external_connectors_enabled:
        raise AgentActionError("External connector actions are disabled.")
    context = decode_action_context_token(
        settings,
        action_context_token,
        expected_tenant_id=tenant_id,
        db=db,
    )
    require_write_actor(db, tenant_id, context.user_id)
    if not target_summary.strip() or len(target_summary.strip()) > 2_000:
        raise AgentActionError("The action preview is missing or too long.")
    try:
        serialized_input = json.dumps(
            action_input,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise AgentActionError("The action input is invalid.") from exc
    if len(serialized_input.encode()) > 64_000:
        raise AgentActionError("The action input exceeds its safe limit.")
    try:
        authorize_mcp_tool(
            db,
            tenant_id=tenant_id,
            connection_id=connection_id,
            tool_name=tool_name,
            write_confirmed=True,
        )
    except McpPermissionDenied as exc:
        raise AgentActionError("The requested connector action is not permitted.") from exc
    idempotency_key = hashlib.sha256(
        (
            f"{tenant_id}\0{context.user_id}\0{context.conversation_id}\0"
            f"{context.request_id}\0{connection_id}\0{tool_name}\0{serialized_input}"
        ).encode()
    ).hexdigest()
    existing = db.scalar(
        select(PendingAgentAction).where(
            PendingAgentAction.tenant_id == tenant_id,
            PendingAgentAction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing, confirmation_token(settings, existing)
    binding = action_binding(db, context)
    for previous in db.scalars(
        select(PendingAgentAction).where(
            PendingAgentAction.tenant_id == tenant_id,
            PendingAgentAction.user_id == context.user_id,
            PendingAgentAction.conversation_id == context.conversation_id,
            PendingAgentAction.status == "pending",
        )
    ):
        if (previous.approval_binding or {}).get(
            "initiating_message_id"
        ) != context.initiating_message_id:
            previous.status = "expired"
    expires_at = utcnow() + timedelta(seconds=ACTION_CONFIRMATION_TTL_SECONDS)
    action = PendingAgentAction(
        id=new_id(),
        tenant_id=tenant_id,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        mcp_connection_id=connection_id,
        tool_name=tool_name,
        target_summary=target_summary.strip(),
        approval_binding=binding,
        authorization_revision=authorization_revision(db, tenant_id, connection_id, tool_name),
        action_input=action_input,
        status="pending",
        idempotency_key=idempotency_key,
        confirmation_token_hash="0" * 64,
        expires_at=expires_at,
    )
    token = confirmation_token(settings, action)
    action.confirmation_token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add(action)
    db.add(
        OperationalAgentEvent(
            tenant_id=tenant_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            channel=context.channel,
            event_type="action_confirmation",
            status="pending",
            tool_name=tool_name,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(
            select(PendingAgentAction).where(
                PendingAgentAction.tenant_id == tenant_id,
                PendingAgentAction.idempotency_key == idempotency_key,
            )
        )
        if duplicate is None:
            raise
        return duplicate, confirmation_token(settings, duplicate)
    return action, token


def confirmation_token(settings: Settings, action: PendingAgentAction) -> str:
    encoded = _encode_json(
        {
            "action_id": action.id,
            "tenant_id": action.tenant_id,
            "user_id": action.user_id,
            "expires_at": int(_as_utc(action.expires_at).timestamp()),
            "exact_effect_hash": hashlib.sha256(
                json.dumps(
                    {
                        "input": action.action_input,
                        "tool": action.tool_name,
                        "target": action.target_summary,
                        "connection": action.mcp_connection_id,
                        "binding": action.approval_binding or {},
                        "authorization_revision": action.authorization_revision or "",
                    },
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest(),
        }
    )
    return f"{encoded}.{_signature(settings, 'confirmation', encoded)}"


def action_preview(action: PendingAgentAction) -> str:
    """Return the exact, user-visible write payload without credentials."""

    exact_input = json.dumps(
        action.action_input,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    return (
        f"External action: {action.tool_name}\n"
        f"Target and effect: {action.target_summary}\n"
        "Exact content and parameters:\n"
        f"{exact_input}\n\n"
        "Nothing has been written yet. Confirming executes this exact action once."
    )


def decide_pending_action(
    db: Session,
    settings: Settings,
    *,
    action_id: str,
    tenant_id: str,
    user_id: str,
    supplied_token: str,
    decision: str,
) -> PendingAgentAction:
    action = db.scalar(
        select(PendingAgentAction)
        .where(
            PendingAgentAction.id == action_id,
            PendingAgentAction.tenant_id == tenant_id,
            PendingAgentAction.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if action is None:
        raise AgentActionError("The pending action was not found.")
    _verify_confirmation_token(settings, action, supplied_token)
    if action.status == "succeeded":
        return action
    if action.status != "pending":
        raise AgentActionError(f"The action is already {action.status}.")
    if _as_utc(action.expires_at) <= utcnow():
        action.status = "expired"
        db.commit()
        raise AgentActionError("The action confirmation has expired.")
    if decision == "deny":
        action.status = "denied"
        db.add(_action_event(action, status="denied"))
        db.commit()
        return action
    if decision != "confirm":
        raise AgentActionError("The action decision is invalid.")
    _validate_bound_source(db, action)
    require_write_actor(db, tenant_id, user_id)
    if not action.authorization_revision or action.authorization_revision != authorization_revision(
        db, tenant_id, action.mcp_connection_id or "", action.tool_name
    ):
        raise AgentActionError(
            "Connector permissions, credentials or business facts changed; review a fresh preview."
        )
    action.status = "executing"
    action.confirmed_at = utcnow()
    db.add(_action_event(action, status="confirmed"))
    db.commit()
    try:
        if not action.mcp_connection_id:
            raise AgentActionError("The connector action has no active connection.")
        from app.jobs.runtime import assert_current_job_lease

        def before_dispatch() -> None:
            current = db.scalar(
                select(PendingAgentAction)
                .where(
                    PendingAgentAction.id == action_id,
                    PendingAgentAction.tenant_id == tenant_id,
                    PendingAgentAction.user_id == user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                not current
                or current.status != "executing"
                or _as_utc(current.expires_at) <= utcnow()
            ):
                raise AgentActionError("The exact action approval is no longer active.")
            _verify_confirmation_token(settings, current, supplied_token)
            _validate_bound_source(db, current)
            require_write_actor(db, tenant_id, user_id)
            assert_current_job_lease(db)
            if current.authorization_revision != authorization_revision(
                db, tenant_id, current.mcp_connection_id or "", current.tool_name
            ):
                raise AgentActionError("Connector authorization changed; review a fresh preview.")

        before_dispatch()
        result = execute_connector_write(
            db,
            settings,
            tenant_id=tenant_id,
            connection_id=action.mcp_connection_id,
            tool_name=action.tool_name,
            tool_input=action.action_input,
            idempotency_key=action.idempotency_key,
            before_dispatch=before_dispatch,
        )
    except (ConnectorExecutionError, McpPermissionDenied, AgentActionError) as exc:
        action.status = "failed"
        action.action_result = {"error": str(exc)}
        action.executed_at = utcnow()
        db.add(_action_event(action, status="failed", error_code="connector_write_failed"))
        db.commit()
        raise AgentActionError(str(exc)) from exc
    action.status = "succeeded"
    action.action_result = result
    action.executed_at = utcnow()
    db.add(_action_event(action, status="succeeded"))
    db.commit()
    return action


def _verify_confirmation_token(
    settings: Settings,
    action: PendingAgentAction,
    supplied_token: str,
) -> None:
    expected = confirmation_token(settings, action)
    supplied_hash = hashlib.sha256(supplied_token.encode()).hexdigest()
    if not (
        hmac.compare_digest(supplied_token, expected)
        and hmac.compare_digest(supplied_hash, action.confirmation_token_hash)
    ):
        raise AgentActionError("The action confirmation is invalid.")


def _action_event(
    action: PendingAgentAction,
    *,
    status: str,
    error_code: str | None = None,
) -> OperationalAgentEvent:
    return OperationalAgentEvent(
        tenant_id=action.tenant_id,
        user_id=action.user_id,
        conversation_id=action.conversation_id,
        channel="agent_action",
        event_type="action_confirmation",
        status=status,
        tool_name=action.tool_name,
        error_code=error_code,
    )


def _signature(settings: Settings, purpose: str, value: str) -> str:
    digest = hmac.new(
        settings.internal_service_token.encode(),
        f"{purpose}\0{value}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _encode_json(value: dict[str, Any]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
        .decode()
        .rstrip("=")
    )


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
