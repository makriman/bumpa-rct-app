from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Literal, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    BumpaMetricSnapshot,
    Conversation,
    GeneratedAgentMedia,
    HermesProfile,
    OperationalAgentEvent,
    Tenant,
    UsageEvent,
    User,
)
from app.providers.hermes import (
    HermesClient,
    HermesEndpoint,
    HermesError,
    HermesResult,
)
from app.providers.local import LocalAgentRuntime, LocalClassifier
from app.providers.redaction import redact_text
from app.services.admin_operations import record_hermes_call_error
from app.services.agent_actions import create_action_context_token
from app.services.agent_execution import ExecutionDenied, begin_run, finish_run, require_active_run
from app.services.bumpa_freshness import latest_available_metrics, latest_complete_orders_run
from app.services.business_onboarding import chat_reply, memory_context
from app.services.business_tools import business_profile, data_coverage
from app.services.conversation_scope import receiver_scope
from app.services.research_events import record_research_event


def build_business_context(db: Session, tenant_id: str) -> tuple[str, object | None]:
    tenant = db.get(Tenant, tenant_id)
    merchant_context = memory_context(db, tenant) if tenant is not None else ""
    metric_keys = (
        "sales.total_sales",
        "sales.gross_profit",
        "sales.net_profit",
        "products.products_sold",
        "products.top_selling_products",
        "customers.top_customers_order",
    )
    fresh_metrics = latest_available_metrics(db, tenant_id, metric_keys=metric_keys)
    orders_run = latest_complete_orders_run(db, tenant_id)
    if not fresh_metrics and orders_run is None:
        profile_context = business_profile(db, tenant_id)
        return (
            f"Business: {profile_context['business']['name']}; "
            f"store timezone: {profile_context['store']['timezone']}; "
            f"store currency: {profile_context['store']['currency']}. "
            "No synced Bumpa metrics are available yet. Data freshness: unavailable."
            + merchant_context,
            None,
        )
    snapshot = {key: fresh.snapshot for key, fresh in fresh_metrics.items()}
    sales = snapshot.get("sales.total_sales")
    gross_profit = snapshot.get("sales.gross_profit")
    net_profit = snapshot.get("sales.net_profit")
    products = snapshot.get("products.products_sold")
    top_products = snapshot.get("products.top_selling_products")
    top_customers = snapshot.get("customers.top_customers_order")
    metric_freshness = (
        min(_as_utc(fresh.refreshed_at) for fresh in fresh_metrics.values())
        if fresh_metrics
        else None
    )
    orders_freshness = (
        _as_utc(orders_run.finished_at)
        if orders_run is not None and orders_run.finished_at is not None
        else None
    )
    boundaries = [value for value in (metric_freshness, orders_freshness) if value is not None]
    freshness = min(boundaries) if boundaries else None
    profile_context = business_profile(db, tenant_id)
    coverage = data_coverage(db, tenant_id)
    context = (
        f"Business: {profile_context['business']['name']}; "
        f"category: {profile_context['business']['category'] or 'unavailable'}; "
        f"location: {profile_context['business']['city'] or 'unavailable'}, "
        f"{profile_context['business']['country'] or 'unavailable'}; "
        f"store timezone: {profile_context['store']['timezone']}; "
        f"store currency: {profile_context['store']['currency']}. "
        f"Total sales: {_format_metric(sales, money=True)}; "
        f"gross profit: {_format_metric(gross_profit, money=True)}; "
        f"net profit: {_format_metric(net_profit, money=True)}; "
        f"products sold: {_format_metric(products)}; "
        f"top products: {_format_rankings(top_products)}; "
        f"customer leaders: {_format_rankings(top_customers)}; "
        f"orders in current snapshot: {_format_count(orders_run.orders_count if orders_run else None)}; "
        f"metrics refreshed: {_format_timestamp(metric_freshness)}; "
        f"orders refreshed: {_format_timestamp(orders_freshness)}; "
        f"conservative data boundary: {_format_timestamp(freshness)}; "
        f"canonical order coverage: {coverage['orders']['date_from'] or 'unavailable'} "
        f"to {coverage['orders']['date_to'] or 'unavailable'} "
        f"({coverage['orders']['count']} orders). "
        "Use the business-data tools for any requested period; do not treat these snapshot "
        "metrics as the requested period unless their exact bounds match."
    )
    return context + merchant_context, freshness


def data_freshness_at_message(
    db: Session,
    *,
    tenant_id: str,
    message_created_at: datetime,
) -> datetime | None:
    """Reconstruct the freshness value returned with an idempotent chat response."""

    metrics = latest_available_metrics(
        db,
        tenant_id,
        metric_keys=(
            "sales.total_sales",
            "sales.gross_profit",
            "sales.net_profit",
            "products.products_sold",
            "products.top_selling_products",
            "customers.top_customers_order",
        ),
        as_of=message_created_at,
    )
    orders_run = latest_complete_orders_run(db, tenant_id, as_of=message_created_at)
    metric_boundary = min((_as_utc(row.refreshed_at) for row in metrics.values()), default=None)
    boundaries = [
        value
        for value in (
            metric_boundary,
            _as_utc(orders_run.finished_at)
            if orders_run is not None and orders_run.finished_at is not None
            else None,
        )
        if value is not None
    ]
    return min(boundaries) if boundaries else None


def _format_metric(metric: BumpaMetricSnapshot | None, *, money: bool = False) -> str:
    if metric is None or metric.availability != "available":
        return "unavailable"
    return _format_decimal(
        metric.value_decimal,
        money=money,
        currency_code=metric.currency_code,
    )


def _format_rankings(metric: BumpaMetricSnapshot | None) -> str:
    if metric is None or metric.availability != "available":
        return "unavailable"
    canonical = metric.canonical_payload
    if (
        canonical.get("schema_version") != 1
        or canonical.get("kind") != "ranking"
        or not isinstance(canonical.get("groups"), list)
    ):
        return "unavailable"
    rendered: list[str] = []
    for group in canonical["groups"][:2]:
        if not isinstance(group, dict) or not isinstance(group.get("rows"), list):
            continue
        for row in group["rows"][:3]:
            if not isinstance(row, dict):
                continue
            label, value = row.get("label"), row.get("value")
            if isinstance(label, str) and isinstance(value, str):
                rendered.append(f"{label} ({value})")
    return ", ".join(rendered) if rendered else "none in selected period"


def _format_count(value: int | None) -> str:
    return "unavailable" if value is None else str(value)


def _format_decimal(
    value: Decimal | None, *, money: bool = False, currency_code: str | None = None
) -> str:
    if value is None:
        return "unavailable"
    if money:
        amount = f"{value:,.2f}"
        return f"{currency_code} {amount}" if currency_code else amount
    return format(value.normalize(), "f")


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unavailable"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def handle_chat(
    db: Session,
    *,
    tenant: Tenant,
    user: User,
    message: str,
    channel: str,
    conversation_id: str | None = None,
    external_message_id: str | None = None,
    provider_content_parts: list[dict[str, object]] | None = None,
    stored_content_parts: list[dict[str, object]] | None = None,
    reply_to_external_message_id: str | None = None,
    media_metadata: dict[str, object] | None = None,
    event_callback: Callable[[str, dict[str, object]], None] | None = None,
    fixed_response: str | None = None,
    whatsapp_identity: tuple[str, str] | None = None,
    settings: Settings | None = None,
    receiver_waba_id: str = "",
    receiver_phone_id: str = "",
    environment_lane: str | None = None,
) -> tuple[Conversation, AgentMessage, AgentMessage, object | None]:
    effective_settings = settings or get_settings()
    lane = environment_lane or effective_settings.app_env
    if channel == "whatsapp":
        if (media_metadata or {}).get("type") == "voice_call":
            receiver_waba_id = effective_settings.meta_waba_id or receiver_waba_id
            receiver_phone_id = effective_settings.meta_phone_number_id or receiver_phone_id
        if effective_settings.whatsapp_backend == "meta" and not (
            receiver_waba_id and receiver_phone_id
        ):
            raise HTTPException(status_code=400, detail="Verified receiving bot is required")
        receiver_waba_id = receiver_waba_id or "mock"
        receiver_phone_id = receiver_phone_id or "mock"
    profile = db.scalar(
        select(HermesProfile).where(
            HermesProfile.tenant_id == tenant.id,
            HermesProfile.status == "active",
        )
    )
    if not profile:
        raise HTTPException(status_code=409, detail="Agent profile is not provisioned")
    conversation = db.get(Conversation, conversation_id) if conversation_id else None
    if conversation and (
        conversation.tenant_id != tenant.id
        or conversation.user_id != user.id
        or conversation.channel != channel
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation and conversation.context_generation != 1:
        raise HTTPException(
            status_code=409, detail="Historical context requires review. Start a new conversation."
        )
    if (
        conversation
        and conversation.environment_lane
        and (
            conversation.environment_lane != lane
            or conversation.receiver_waba_id != receiver_waba_id
            or conversation.receiver_phone_id != receiver_phone_id
        )
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    follow_up_detected = conversation is not None
    if not conversation:
        conversation = Conversation(
            tenant_id=tenant.id,
            user_id=user.id,
            channel=channel,
            # Voice owns a session explicitly. The text-message router selects
            # only open conversations and must never pick an in-progress call.
            status="voice" if (media_metadata or {}).get("type") == "voice_call" else "open",
            title=message[:80],
            context_generation=1,
            receiver_waba_id=receiver_waba_id,
            receiver_phone_id=receiver_phone_id,
            environment_lane=lane,
        )
        db.add(conversation)
        db.flush()
    if not conversation.environment_lane:
        # Only newly created/model-default generation1 rows may acquire scope.
        # Migrated historical generation0 rows already failed closed above.
        conversation.environment_lane = lane
        conversation.receiver_waba_id = receiver_waba_id
        conversation.receiver_phone_id = receiver_phone_id
    if not conversation.provider_session_key:
        conversation.provider_session_key = _provider_session_key(
            effective_settings,
            tenant_id=tenant.id,
            user_id=user.id,
            channel=channel,
            conversation_id=conversation.id,
            receiver_waba_id=receiver_waba_id,
            receiver_phone_id=receiver_phone_id,
            environment_lane=lane,
        )
    history = _conversation_history(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        limit=effective_settings.hermes_history_messages,
    )
    inbound = AgentMessage(
        tenant_id=tenant.id,
        user_id=user.id,
        hermes_profile_id=profile.id,
        conversation_id=conversation.id,
        channel=channel,
        direction="inbound",
        content=message,
        content_parts=stored_content_parts or [],
        reply_to_external_message_id=reply_to_external_message_id,
        media_metadata=media_metadata or {},
        redacted_content=redact_text(message),
        external_message_id=external_message_id,
        event_scope=receiver_scope(lane, (receiver_waba_id, receiver_phone_id)),
    )
    db.add(inbound)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This input was already admitted; reconcile its existing result"
        ) from exc
    event_source = external_message_id or inbound.id
    confirmed_utterance = None
    pending = conversation.pending_utterance or {}
    confirmation = re.sub(r"[^a-z ]", "", message.lower()).strip() in {
        "yes",
        "correct",
        "it is",
        "its correct",
        "no its correct",
        "confirm its correct answer it",
        "yes correct",
    }
    if (
        pending
        and confirmation
        and pending.get("phone_identity_id") != (media_metadata or {}).get("phone_identity_id")
    ):
        fixed_response = "Please confirm that voice note from the same approved number, or type your request here."
    elif pending and confirmation:
        if int(pending.get("expires_at", 0)) > int(utcnow().timestamp()):
            confirmed_utterance = str(pending["transcript"])
        else:
            fixed_response = "That voice confirmation expired. Please repeat or type your request."
        conversation.pending_utterance = {}
    elif pending:
        conversation.pending_utterance = {}
    if (media_metadata or {}).get("clarification_required"):
        transcript = next(
            (
                part.get("text")
                for part in (stored_content_parts or [])
                if part.get("type") == "transcript" and isinstance(part.get("text"), str)
            ),
            None,
        )
        if transcript:
            conversation.pending_utterance = {
                "source_message_id": inbound.id,
                "phone_identity_id": (media_metadata or {}).get("phone_identity_id"),
                "transcript": transcript,
                "expires_at": int((utcnow() + timedelta(minutes=10)).timestamp()),
            }
    share_memory_command = re.fullmatch(
        r"SHARE MEMORY ([A-Za-z0-9-]+) ([a-f0-9]{12})", message.strip()
    )
    private_memory_command = re.fullmatch(
        r"REMEMBER PRIVATE ([A-Za-z0-9_.-]+) ([0-9]+)\n([\s\S]+)", message.strip()
    )
    forget_memory_command = re.fullmatch(
        r"FORGET MEMORY (private|shared) ([A-Za-z0-9_.-]+) ([0-9]+)", message.strip()
    )
    memory_command = share_memory_command or private_memory_command or forget_memory_command
    if (
        not memory_command
        and fixed_response is None
        and confirmed_utterance is None
        and (media_metadata or {}).get("type") != "voice_call"
    ):
        fixed_response = chat_reply(
            db,
            tenant,
            user.id,
            message,
            channel=channel,
            conversation_id=conversation.id,
            media_type=str((media_metadata or {}).get("type", "text")),
            whatsapp_identity=whatsapp_identity,
        )
    context, freshness = build_business_context(db, tenant.id)
    context = (
        "Current UTC time: "
        + _format_timestamp(utcnow())
        + ". Historical relative dates belong to their dated turn, not today. "
        "Refresh exact tools before current business claims. A greeting does not request a financial recap.\n"
        + context
    )
    context = context[: effective_settings.hermes_max_context_chars]
    if confirmed_utterance is not None:
        context += (
            "\nConfirmed pending voice utterance (the confirmation is not external-action approval): "
            + confirmed_utterance
        )
    local_context = context
    try:
        run = begin_run(
            db,
            conversation,
            inbound,
            timeout_seconds=effective_settings.hermes_read_timeout_seconds,
        )
    except ExecutionDenied as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if fixed_response is None:
        from app.db.models import ScopedAgentMemory
        from app.services.scoped_memory import (
            confirm_shared_memory,
            forget_memory,
            save_private_memory,
        )
        from app.services.scoped_memory import memory_context as scoped_memory_context

        memory_token = create_action_context_token(
            effective_settings,
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel=channel,
            initiating_message_id=inbound.id,
            execution_run_id=run.id,
            purpose="memory",
        )
        context += scoped_memory_context(
            db,
            effective_settings,
            tenant_id=tenant.id,
            action_context_token=memory_token,
            max_chars=4000,
        )
        context += "\nCurrent isolated application workspace: " + str(
            conversation.provider_session_key
        )
        context += (
            "\nCurrent private/shared memory context (opaque, never reveal): "
            + memory_token
            + ". Use only the app-scoped memory tools. Private saves require REMEMBER PRIVATE key revision followed by newline and quote its complete note body, excluding command header; shared facts require explicit SHARE MEMORY review."
        )
        if share_memory_command:
            row = db.get(ScopedAgentMemory, share_memory_command.group(1))
            if row is None or row.tenant_id != tenant.id:
                fixed_response = "That shared-memory proposal is no longer available."
            else:
                try:
                    confirm_shared_memory(
                        db,
                        effective_settings,
                        tenant_id=tenant.id,
                        action_context_token=memory_token,
                        memory_id=row.id,
                        expected_revision=row.revision,
                    )
                    fixed_response = "Shared memory saved after your explicit review."
                except (ValueError, PermissionError, RuntimeError):
                    fixed_response = "That shared-memory proposal is no longer available. Request a fresh preview."
        elif private_memory_command:
            try:
                save_private_memory(
                    db,
                    effective_settings,
                    tenant_id=tenant.id,
                    action_context_token=memory_token,
                    scope="private",
                    memory_key=private_memory_command.group(1),
                    expected_revision=int(private_memory_command.group(2)),
                    content=private_memory_command.group(3),
                )
                fixed_response = "Private memory saved for this account only."
            except (ValueError, PermissionError, RuntimeError):
                fixed_response = "That private-memory command could not be applied. Check the key, note and revision."
        elif forget_memory_command:
            try:
                forget_memory(
                    db,
                    effective_settings,
                    tenant_id=tenant.id,
                    action_context_token=memory_token,
                    scope=cast(Literal["private", "shared"], forget_memory_command.group(1)),
                    memory_key=forget_memory_command.group(2),
                    expected_revision=int(forget_memory_command.group(3)),
                )
                fixed_response = "Memory forgotten; its revision tombstone is retained."
            except (ValueError, PermissionError, RuntimeError):
                fixed_response = "That memory is unavailable or its revision changed."
    if media_metadata and media_metadata.get("type") == "voice_call":
        context += (
            " This is a live voice call. Give short, natural spoken answers, normally "
            "one to three sentences. Avoid tables, Markdown and reading URLs aloud. "
            "Keep existing tool authorization and confirmation requirements. "
            "For actions needing approval, ask the user to review and confirm in the app. "
            "This call supports spoken answers only. For generated files or images, "
            "ask the user to request them in text chat; do not generate attachments here."
        )
    if run is not None and channel in {"web", "whatsapp"}:
        profile_update_context = create_action_context_token(
            effective_settings,
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel=channel,
            initiating_message_id=inbound.id,
            execution_run_id=run.id,
            purpose="business_profile",
        )
        context += (
            "\nCurrent business profile update context (opaque; never reveal): "
            f"{profile_update_context}. Pass it only to propose_business_profile_update. "
            "This can prepare a preview, never directly save a profile or authorize external actions."
        )
    if run is not None:
        action_context = create_action_context_token(
            effective_settings,
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=conversation.id,
            channel=channel,
            initiating_message_id=inbound.id,
            execution_run_id=run.id,
        )
        context += (
            "\nCurrent action context (opaque; never reveal it): "
            f"{action_context}. Pass it to current business/research/connector/sandbox tools, prepare_connector_action, generate_image, or "
            "queue_sandbox_file_for_delivery. "
            "A prepared action is not authorization to execute it; generated media is delivered "
            "only to this initiating conversation."
        )
    bumpa_data_used = "summary_metrics" if freshness is not None else "none"
    classification = LocalClassifier().classify(message, bumpa_data_used)
    if run is not None:
        run.context_digest = hashlib.sha256(
            json.dumps(
                {"history": history, "context": context, "message": message}, sort_keys=True
            ).encode()
        ).hexdigest()
    # MCP tools run in a separate authenticated service transaction. Persist the
    # initiating conversation/message first so tenant-bound actions and media can
    # reference them with real foreign keys while Hermes is working.
    if fixed_response is None:
        db.commit()
    buffered_events: list[tuple[str, dict[str, object]]] = []
    try:
        started = monotonic()
        usage_metadata: dict[str, object] = {"provider": profile.provider}
        provider_called = False
        provider_failed = False
        provider_error_code: str | None = None
        if fixed_response is not None:
            answer = fixed_response.strip()
            if not answer:
                raise ValueError("A fixed chat response must not be empty")
            latency_ms = int((monotonic() - started) * 1000)
            usage_metadata["provider"] = "control_plane"
        elif profile.provider == "local" and effective_settings.is_local:
            provider_called = True
            answer = LocalAgentRuntime().respond(profile.profile_name, message, local_context)
            latency_ms = int((monotonic() - started) * 1000)
        elif profile.provider == "hermes" and effective_settings.agent_backend == "hermes":
            provider_called = True
            try:
                api_key = FieldCipher.from_settings(effective_settings).decrypt(
                    profile.encrypted_api_key
                )
                hermes_client = HermesClient(effective_settings)
                respond_method = (
                    hermes_client.respond_stream
                    if event_callback is not None
                    and callable(getattr(hermes_client, "respond_stream", None))
                    else hermes_client.respond
                )
                respond_options: dict[str, object] = {
                    "channel": channel,
                    "history": history,
                    "request_id": inbound.id,
                    "on_prepared": lambda request: _record_provider_request(
                        db, run, request, effective_settings
                    ),
                    "on_admitted": lambda provider_id: _record_provider_admission(
                        db, run, provider_id
                    ),
                    "content_parts": provider_content_parts,
                    "session_id": conversation.provider_session_id,
                    "session_key": conversation.provider_session_key,
                    "on_event": (lambda name, event: buffered_events.append((name, event)))
                    if event_callback
                    else None,
                }
                supported_options = inspect.signature(respond_method).parameters
                respond_call = cast(Callable[..., HermesResult], respond_method)
                result = respond_call(
                    HermesEndpoint(
                        profile_name=profile.profile_name,
                        api_url=profile.api_internal_url,
                        api_key=api_key,
                    ),
                    message=redact_text(message),
                    business_context=context,
                    **{
                        key: value
                        for key, value in respond_options.items()
                        if key in supported_options
                    },
                )
            except HermesError as exc:
                _persist_failed_hermes_call(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    profile_id=profile.id,
                    conversation_id=conversation.id,
                    channel=channel,
                    event_source=event_source,
                    message=message,
                    classification=classification,
                    follow_up_detected=follow_up_detected,
                    bumpa_context_available=freshness is not None,
                    error_code=exc.code,
                    retryable=exc.retryable,
                    system_error=True,
                )
                provider_called = False
                provider_failed = True
                provider_error_code = exc.code
                usage_metadata["provider"] = "control_plane"
                answer = _provider_failure_response(freshness)
                latency_ms = int((monotonic() - started) * 1000)
            except ValueError:
                _persist_failed_hermes_call(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    profile_id=profile.id,
                    conversation_id=conversation.id,
                    channel=channel,
                    event_source=event_source,
                    message=message,
                    classification=classification,
                    follow_up_detected=follow_up_detected,
                    bumpa_context_available=freshness is not None,
                    error_code="profile_key_invalid",
                    retryable=False,
                    system_error=False,
                )
                provider_called = False
                provider_failed = True
                provider_error_code = "profile_key_invalid"
                usage_metadata["provider"] = "control_plane"
                answer = _provider_failure_response(freshness)
                latency_ms = int((monotonic() - started) * 1000)
            else:
                answer = result.content
                if result.session_id:
                    conversation.provider_session_id = result.session_id[:160]
                latency_ms = result.latency_ms
                usage_metadata.update(
                    {
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "total_tokens": result.total_tokens,
                    }
                )
        else:
            provider_failed = True
            provider_error_code = "agent_runtime_unavailable"
            usage_metadata["provider"] = "control_plane"
            answer = _provider_failure_response(freshness)
            latency_ms = int((monotonic() - started) * 1000)
        if run is not None:
            run = require_active_run(
                db,
                run_id=run.id,
                tenant_id=tenant.id,
                user_id=user.id,
                conversation_id=conversation.id,
                initiating_message_id=inbound.id,
                channel=channel,
            )
            if run.output_message_id or reply_for_input(
                db, tenant_id=tenant.id, user_id=user.id, inbound_id=inbound.id
            ):
                raise ExecutionDenied("This input already has an accepted reply")
        outbound = AgentMessage(
            tenant_id=tenant.id,
            user_id=user.id,
            hermes_profile_id=profile.id,
            conversation_id=conversation.id,
            channel=channel,
            direction="outbound",
            initiating_message_id=inbound.id,
            media_metadata={
                "event_kind": "reply",
                "execution_run_id": run.id if run else None,
                "model_outcome": "failed" if provider_failed else "completed",
                **(
                    {"type": "voice_call", "voice_playback_state": "generated"}
                    if (media_metadata or {}).get("type") == "voice_call"
                    else {}
                ),
            },
            content=answer,
            content_parts=[{"type": "text", "text": answer}],
            redacted_content=redact_text(answer),
            latency_ms=latency_ms,
        )
        db.add(outbound)
        # Materialize the outbound identifier before research instrumentation so
        # the response event can prove where permissioned raw text is retained.
        db.flush()
        generated_media = db.scalars(
            select(GeneratedAgentMedia).where(
                GeneratedAgentMedia.tenant_id == tenant.id,
                GeneratedAgentMedia.user_id == user.id,
                GeneratedAgentMedia.conversation_id == conversation.id,
                GeneratedAgentMedia.channel == channel,
                GeneratedAgentMedia.agent_message_id == inbound.id,
                GeneratedAgentMedia.execution_run_id == (run.id if run is not None else None),
            )
        ).all()
        for media in generated_media:
            media.agent_message_id = outbound.id
        if run is not None:
            run.output_message_id = outbound.id
            finish_run(db, run, status="failed" if provider_failed else "completed")
        conversation.updated_at = utcnow()
        _record_successful_chat_events(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            profile=profile,
            conversation_id=conversation.id,
            inbound=inbound,
            outbound=outbound,
            event_source=event_source,
            channel=channel,
            message=message,
            answer=answer,
            classification=classification,
            follow_up_detected=follow_up_detected,
            bumpa_context_available=freshness is not None,
            latency_ms=latency_ms,
            provider_called=provider_called,
        )
        db.add(
            UsageEvent(
                tenant_id=tenant.id,
                user_id=user.id,
                event_name="chat.response",
                units=(
                    Decimal(str(usage_metadata["total_tokens"]))
                    if "total_tokens" in usage_metadata
                    else None
                ),
                event_metadata={**usage_metadata, "latency_ms": latency_ms},
            )
        )
        db.add(
            OperationalAgentEvent(
                tenant_id=tenant.id,
                user_id=user.id,
                conversation_id=conversation.id,
                channel=channel,
                event_type="assistant_response",
                status="degraded" if provider_failed else "succeeded",
                media_type=(
                    str((media_metadata or {}).get("type"))
                    if (media_metadata or {}).get("type")
                    else None
                ),
                duration_ms=latency_ms,
                error_code=provider_error_code,
                citation_count=len(re.findall(r"https://[^\s)>\]]+", answer)),
                grounding_flags=[
                    flag
                    for flag, present in (
                        ("tenant_data", freshness is not None),
                        ("external_citation", "https://" in answer),
                        ("multimodal", bool(provider_content_parts)),
                        ("provider_outage", provider_failed),
                    )
                    if present
                ],
            )
        )
        db.commit()
        db.refresh(inbound)
        db.refresh(outbound)
        if event_callback is not None:
            for name, event in buffered_events:
                event_callback(name, event)
        return conversation, inbound, outbound, freshness
    except Exception:
        db.rollback()
        if run is not None:
            current_run = db.get(type(run), run.id)
            current_conversation = db.scalar(
                select(Conversation)
                .where(Conversation.id == conversation.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                current_run is not None
                and current_run.status == "running"
                and current_conversation is not None
                and current_conversation.active_run_id == current_run.id
            ):
                if current_run.request_snapshots_encrypted:
                    # Preserve the ordering anchor for GET-only reconciliation;
                    # an exception cannot establish that admission never happened.
                    current_run.status = "expired"
                    current_run.expires_at = utcnow()
                else:
                    finish_run(db, current_run, status="failed")
                db.commit()
        raise


def _record_provider_request(
    db: Session, run: object, request: dict[str, object], settings: Settings
) -> None:
    from app.db.models import AgentExecutionRun
    from app.services.run_reconciliation import freeze_request

    if not isinstance(run, AgentExecutionRun):
        return
    bound_conversation = db.get(Conversation, run.conversation_id)
    if bound_conversation is None:
        raise ExecutionDenied("Run conversation is unavailable")
    run = require_active_run(
        db,
        run_id=run.id,
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        conversation_id=run.conversation_id,
        initiating_message_id=run.initiating_message_id,
        channel=bound_conversation.channel,
    )
    frozen = freeze_request(request)
    cipher = FieldCipher.from_settings(settings)
    retained = (
        json.loads(cipher.decrypt(run.request_snapshots_encrypted))
        if run.request_snapshots_encrypted
        else []
    )
    serialized = frozen.payload_json
    if "data:image/" in serialized or len(serialized) > 512_000:
        retained.append(
            {
                "sha256": frozen.sha256,
                "recoverable": False,
                "reason": "binary_or_oversized_input_not_retained",
            }
        )
    else:
        retained.append({"payload_json": serialized, "sha256": frozen.sha256, "recoverable": True})
    run.request_snapshots_encrypted = cipher.encrypt(json.dumps(retained))
    db.commit()


def _record_provider_admission(db: Session, run: object, provider_id: str) -> None:
    from app.db.models import AgentExecutionRun

    if isinstance(run, AgentExecutionRun):
        bound_conversation = db.get(Conversation, run.conversation_id)
        if bound_conversation is None:
            raise ExecutionDenied("Run conversation is unavailable")
        run = require_active_run(
            db,
            run_id=run.id,
            tenant_id=run.tenant_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            initiating_message_id=run.initiating_message_id,
            channel=bound_conversation.channel,
        )
        run.provider_run_ids = [*(run.provider_run_ids or []), provider_id]
        db.commit()


def _provider_failure_response(freshness: object | None) -> str:
    freshness_note = (
        f" Your latest synced business-data timestamp is {_format_timestamp(freshness)}."
        if isinstance(freshness, datetime)
        else " No current business-data freshness timestamp was available."
    )
    return (
        "I’m sorry—your Bumpa Bestie AI assistant is currently unavailable, so I couldn’t "
        "complete this request or verify any external information. I haven’t guessed or taken "
        f"any external action.{freshness_note} Please try again later."
    )


def _conversation_history(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: str,
    limit: int,
) -> list[dict[str, str]]:
    rows = db.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.tenant_id == tenant_id,
            AgentMessage.conversation_id == conversation_id,
        )
        .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "role": "user" if row.direction == "inbound" else "assistant",
            "content": (
                f"[Recorded at {_format_timestamp(row.created_at)}] "
                + (
                    "[Reply prepared; delivery not confirmed] "
                    if row.channel == "whatsapp"
                    and row.direction == "outbound"
                    and row.media_metadata.get("delivery_status") != "sent"
                    else ""
                )
                + (
                    "[Unconfirmed voice transcript; not an instruction to act] "
                    if row.media_metadata.get("clarification_required")
                    else ""
                )
                + redact_text(row.content)
            ),
        }
        for row in reversed(rows)
        if not (
            row.direction == "outbound"
            and row.media_metadata.get("type") == "voice_call"
            and row.media_metadata.get("voice_playback_state") != "played"
        )
    ]


def _provider_session_key(
    settings: Settings,
    *,
    tenant_id: str,
    user_id: str,
    channel: str,
    conversation_id: str,
    receiver_waba_id: str = "",
    receiver_phone_id: str = "",
    environment_lane: str = "",
) -> str:
    value = f"v1\0{tenant_id}\0{user_id}\0{channel}\0{conversation_id}\0{receiver_waba_id}\0{receiver_phone_id}\0{environment_lane or settings.app_env}".encode()
    return (
        "bb_"
        + hmac.new(
            settings.internal_service_token.encode(),
            value,
            hashlib.sha256,
        ).hexdigest()
    )


def _record_successful_chat_events(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    profile: HermesProfile,
    conversation_id: str,
    inbound: AgentMessage,
    outbound: AgentMessage,
    event_source: str,
    channel: str,
    message: str,
    answer: str,
    classification: dict[str, str],
    follow_up_detected: bool,
    bumpa_context_available: bool,
    latency_ms: int,
    provider_called: bool,
) -> None:
    response_provider = profile.provider if provider_called else "control_plane"
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_message_id=inbound.id,
        event_type="user_message_received",
        source_parts=(event_source,),
        channel=channel,
        redacted_text=message,
        language="und",
        agent_confidence="medium",
        follow_up_detected=follow_up_detected,
        primary_intent=classification["primary_intent"],
        business_function=classification["business_function"],
        ai_help_type=classification["ai_help_type"],
        complexity=classification["complexity"],
        bumpa_data_used=classification["bumpa_data_used"],
        classification_version=classification["classification_version"],
    )
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event_type="bumpa_context_built",
        source_parts=(event_source,),
        channel=channel,
        bumpa_data_used=classification["bumpa_data_used"],
        business_outcome={
            "status": "available" if bumpa_context_available else "unavailable",
        },
        quality_flags=() if bumpa_context_available else ("bumpa_data_unavailable",),
    )
    if profile.provider == "hermes" and provider_called:
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_started",
            source_parts=(event_source,),
            channel=channel,
            business_outcome={"status": "started", "provider": "hermes"},
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_completed",
            source_parts=(event_source,),
            channel=channel,
            response_latency_ms=latency_ms,
            business_outcome={"status": "completed", "provider": "hermes"},
        )
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event_type="research_classification_completed",
        source_parts=(event_source,),
        channel=channel,
        agent_confidence="medium",
        follow_up_detected=follow_up_detected,
        business_outcome={"status": "completed"},
        primary_intent=classification["primary_intent"],
        business_function=classification["business_function"],
        ai_help_type=classification["ai_help_type"],
        complexity=classification["complexity"],
        bumpa_data_used=classification["bumpa_data_used"],
        classification_version=classification["classification_version"],
    )
    record_research_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_message_id=outbound.id,
        event_type="assistant_response_sent",
        source_parts=(event_source,),
        channel=channel,
        redacted_text=answer,
        language="und",
        response_length_chars=len(answer),
        response_latency_ms=latency_ms,
        follow_up_detected=follow_up_detected,
        business_outcome={"status": "sent", "provider": response_provider},
        primary_intent=classification["primary_intent"],
        business_function=classification["business_function"],
        ai_help_type=classification["ai_help_type"],
        complexity=classification["complexity"],
        bumpa_data_used=classification["bumpa_data_used"],
        classification_version=classification["classification_version"],
    )


def _persist_failed_hermes_call(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str,
    conversation_id: str | None,
    channel: str,
    event_source: str,
    message: str,
    classification: dict[str, str],
    follow_up_detected: bool,
    bumpa_context_available: bool,
    error_code: str,
    retryable: bool,
    system_error: bool,
) -> None:
    """Persist bounded failure evidence after abandoning partial chat state."""

    db.rollback()
    try:
        if system_error:
            record_hermes_call_error(
                db,
                tenant_id=tenant_id,
                profile_id=profile_id,
                category=error_code,
            )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="user_message_received",
            source_parts=(event_source,),
            channel=channel,
            redacted_text=message,
            language="und",
            agent_confidence="medium",
            follow_up_detected=follow_up_detected,
            quality_flags=("assistant_response_failed",),
            primary_intent=classification["primary_intent"],
            business_function=classification["business_function"],
            ai_help_type=classification["ai_help_type"],
            complexity=classification["complexity"],
            bumpa_data_used=classification["bumpa_data_used"],
            classification_version=classification["classification_version"],
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="bumpa_context_built",
            source_parts=(event_source,),
            channel=channel,
            bumpa_data_used=classification["bumpa_data_used"],
            business_outcome={
                "status": "available" if bumpa_context_available else "unavailable",
            },
            quality_flags=() if bumpa_context_available else ("bumpa_data_unavailable",),
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_started",
            source_parts=(event_source,),
            channel=channel,
            business_outcome={"status": "started", "provider": "hermes"},
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hermes_call_failed",
            source_parts=(event_source,),
            channel=channel,
            business_outcome={
                "status": "failed",
                "provider": "hermes",
                "error_code": error_code,
                "retryable": retryable,
            },
            quality_flags=(error_code,),
        )
        record_research_event(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="research_classification_completed",
            source_parts=(event_source,),
            channel=channel,
            agent_confidence="medium",
            follow_up_detected=follow_up_detected,
            business_outcome={"status": "completed"},
            primary_intent=classification["primary_intent"],
            business_function=classification["business_function"],
            ai_help_type=classification["ai_help_type"],
            complexity=classification["complexity"],
            bumpa_data_used=classification["bumpa_data_used"],
            classification_version=classification["classification_version"],
        )
        db.commit()
    except Exception:
        # Instrumentation must not replace the sanitized provider response with a
        # secondary database error. The original Hermes failure remains primary.
        db.rollback()


def reply_for_input(
    db: Session, *, tenant_id: str, user_id: str, inbound_id: str
) -> AgentMessage | None:
    return db.scalar(
        select(AgentMessage)
        .where(
            AgentMessage.tenant_id == tenant_id,
            AgentMessage.user_id == user_id,
            AgentMessage.direction == "outbound",
            AgentMessage.initiating_message_id == inbound_id,
            AgentMessage.media_metadata["event_kind"].as_string() == "reply",
        )
        .order_by(AgentMessage.created_at, AgentMessage.id)
    )
