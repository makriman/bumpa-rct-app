"""PII-safe operational alert discovery, signing, and delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import (
    AsyncJob,
    BumpaSyncRun,
    HermesProfile,
    SystemError,
    WhatsappDeliveryEvent,
    WhatsappMessage,
)
from app.jobs.runtime import PermanentJobError, enqueue_job
from app.providers.hermes import HermesClient, HermesError, endpoint_for

AlertEventType = Literal[
    "bumpa_sync_failure",
    "whatsapp_delivery_failure",
    "hermes_call_failure",
    "hermes_health_failure",
]
ALERT_EVENT_TYPES = frozenset(
    {
        "bumpa_sync_failure",
        "whatsapp_delivery_failure",
        "hermes_call_failure",
        "hermes_health_failure",
    }
)
SAFE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
MAX_ALERT_BODY_BYTES = 16_384
FAILED_WHATSAPP_STATUSES = frozenset({"failed", "rejected", "ambiguous", "undeliverable"})


class OperationalAlertError(RuntimeError):
    """A sanitized alert-destination failure."""

    def __init__(self, *, retryable: bool) -> None:
        super().__init__("Operational alert delivery failed")
        self.retryable = retryable


@dataclass(frozen=True)
class AlertSource:
    event_type: AlertEventType
    source_type: str
    source_id: str
    occurred_at: datetime
    tenant_id: str | None
    category: str


class OperationalAlertClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        endpoint = settings.ops_alert_webhook_url or ""
        parsed = urlsplit(endpoint)
        if not (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path not in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        ):
            raise ValueError("Operational alert endpoint is invalid")
        secret = settings.effective_ops_alert_hmac_secret
        if len(secret) < 32:
            raise ValueError("Operational alert signing secret is invalid")
        self._endpoint = endpoint
        self._secret = secret.encode("utf-8")
        self._timeout = httpx.Timeout(settings.ops_alert_timeout_seconds)
        self._response_limit = settings.ops_alert_max_response_bytes
        self._transport = transport

    def send(self, envelope: dict[str, object]) -> str:
        body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(body) > MAX_ALERT_BODY_BYTES:
            raise ValueError("Operational alert payload exceeds the safe limit")
        event_id = envelope.get("event_id")
        occurred_at = envelope.get("occurred_at")
        if not isinstance(event_id, str) or not isinstance(occurred_at, str):
            raise ValueError("Operational alert envelope is invalid")
        signature = hmac.new(
            self._secret,
            occurred_at.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                with client.stream(
                    "POST",
                    self._endpoint,
                    content=body,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Idempotency-Key": event_id,
                        "User-Agent": "BumpaBestie-OpsAlerts/1.0",
                        "X-BumpaBestie-Signature": f"v1={signature}",
                        "X-BumpaBestie-Timestamp": occurred_at,
                    },
                ) as response:
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._response_limit:
                            raise OperationalAlertError(retryable=False)
                    if 200 <= response.status_code < 300:
                        return event_id
                    retryable = (
                        response.status_code in {408, 425, 429} or response.status_code >= 500
                    )
                    raise OperationalAlertError(retryable=retryable)
        except OperationalAlertError:
            raise
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise OperationalAlertError(retryable=True) from exc


def discover_operational_alerts(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Create durable, exactly-once alert jobs for recent terminal evidence."""

    if not settings.ops_alerts_enabled:
        return 0
    cutoff = _as_utc(now or utcnow()) - timedelta(hours=settings.ops_alert_scan_lookback_hours)
    sources: list[AlertSource] = []

    sync_runs = db.scalars(
        select(BumpaSyncRun)
        .where(
            BumpaSyncRun.finished_at >= cutoff,
            (BumpaSyncRun.status == "failed") | (BumpaSyncRun.completion_quality == "degraded"),
            _not_already_alerted("bumpa_sync_failure", "bumpa_sync_run", BumpaSyncRun.id),
        )
        .order_by(BumpaSyncRun.finished_at.asc(), BumpaSyncRun.id.asc())
        .limit(settings.ops_alert_scan_limit)
    ).all()
    sources.extend(
        AlertSource(
            event_type="bumpa_sync_failure",
            source_type="bumpa_sync_run",
            source_id=row.id,
            occurred_at=_as_utc(row.finished_at or cutoff),
            tenant_id=row.tenant_id,
            category="degraded" if row.completion_quality == "degraded" else "failed",
        )
        for row in sync_runs
    )

    whatsapp_messages = db.scalars(
        select(WhatsappMessage)
        .where(
            WhatsappMessage.created_at >= cutoff,
            WhatsappMessage.direction == "outbound",
            WhatsappMessage.status.in_(FAILED_WHATSAPP_STATUSES),
            _not_already_alerted(
                "whatsapp_delivery_failure", "whatsapp_message", WhatsappMessage.id
            ),
        )
        .order_by(WhatsappMessage.created_at.asc(), WhatsappMessage.id.asc())
        .limit(settings.ops_alert_scan_limit)
    ).all()
    sources.extend(
        AlertSource(
            event_type="whatsapp_delivery_failure",
            source_type="whatsapp_message",
            source_id=row.id,
            occurred_at=_as_utc(row.created_at),
            tenant_id=row.tenant_id,
            category=row.status if row.status in FAILED_WHATSAPP_STATUSES else "failed",
        )
        for row in whatsapp_messages
    )

    delivery_events = db.execute(
        select(WhatsappDeliveryEvent, WhatsappMessage)
        .outerjoin(WhatsappMessage, WhatsappMessage.id == WhatsappDeliveryEvent.whatsapp_message_id)
        .where(
            WhatsappDeliveryEvent.created_at >= cutoff,
            WhatsappDeliveryEvent.status.in_(FAILED_WHATSAPP_STATUSES),
            _not_already_alerted(
                "whatsapp_delivery_failure",
                "whatsapp_delivery_event",
                WhatsappDeliveryEvent.id,
            ),
        )
        .order_by(WhatsappDeliveryEvent.created_at.asc(), WhatsappDeliveryEvent.id.asc())
        .limit(settings.ops_alert_scan_limit)
    ).all()
    sources.extend(
        AlertSource(
            event_type="whatsapp_delivery_failure",
            source_type="whatsapp_delivery_event",
            source_id=event.id,
            occurred_at=_as_utc(event.created_at),
            tenant_id=message.tenant_id if message else None,
            category=(
                event.status if event.status in FAILED_WHATSAPP_STATUSES else "undeliverable"
            ),
        )
        for event, message in delivery_events
    )

    system_errors = db.scalars(
        select(SystemError)
        .where(
            SystemError.created_at >= cutoff,
            or_(
                and_(
                    SystemError.service == "hermes",
                    _not_already_alerted("hermes_call_failure", "system_error", SystemError.id),
                ),
                and_(
                    SystemError.service == "hermes_health",
                    _not_already_alerted("hermes_health_failure", "system_error", SystemError.id),
                ),
            ),
        )
        .order_by(SystemError.created_at.asc(), SystemError.id.asc())
        .limit(settings.ops_alert_scan_limit)
    ).all()
    for row in system_errors:
        metadata = row.error_metadata if isinstance(row.error_metadata, dict) else {}
        category = metadata.get("category")
        sources.append(
            AlertSource(
                event_type=(
                    "hermes_health_failure"
                    if row.service == "hermes_health"
                    else "hermes_call_failure"
                ),
                source_type="system_error",
                source_id=row.id,
                occurred_at=_as_utc(row.created_at),
                tenant_id=row.tenant_id,
                category=_safe_category(category),
            )
        )

    created = 0
    for source in sources:
        _, was_created = enqueue_alert_job(db, source, settings)
        created += int(was_created)
    return created


def enqueue_alert_job(
    db: Session,
    source: AlertSource,
    settings: Settings,
) -> tuple[object, bool]:
    return enqueue_job(
        db,
        kind="ops.deliver_alert",
        tenant_id=source.tenant_id,
        idempotency_key=f"ops-alert:{source.event_type}:{source.source_type}:{source.source_id}",
        payload={
            "event_type": source.event_type,
            "source_type": source.source_type,
            "source_id": source.source_id,
            "occurred_at": source.occurred_at.isoformat(),
            "tenant_id": source.tenant_id,
            "category": source.category,
        },
        max_attempts=settings.ops_alert_max_attempts,
    )


def deliver_operational_alert(
    payload: dict[str, Any],
    settings: Settings,
    *,
    client: OperationalAlertClient | None = None,
) -> dict[str, object]:
    if not settings.ops_alerts_enabled:
        return {"status": "disabled"}
    source = _source_from_payload(payload)
    envelope = _build_envelope(source, settings.effective_ops_alert_hmac_secret)
    try:
        event_id = (client or OperationalAlertClient(settings)).send(envelope)
    except OperationalAlertError as exc:
        if not exc.retryable:
            raise PermanentJobError("Operational alert destination rejected delivery") from exc
        raise
    return {"status": "delivered", "event_id": event_id, "event_type": source.event_type}


def check_hermes_profile_health(
    db: Session,
    *,
    profile_id: str,
    settings: Settings,
    client: HermesClient | None = None,
) -> dict[str, object]:
    if not settings.ops_alerts_enabled:
        return {"status": "disabled"}
    profile = db.get(HermesProfile, profile_id)
    if profile is None or profile.provider != "hermes" or profile.status == "disabled":
        return {"status": "skipped"}
    category: str | None = None
    try:
        readiness = (client or HermesClient(settings)).readiness(endpoint_for(profile, settings))
        if readiness.ready:
            if profile.status == "degraded":
                profile.status = "active"
            return {"status": "healthy", "latency_ms": readiness.latency_ms}
        category = "not_ready"
    except HermesError as exc:
        category = _safe_category(exc.code)
    except ValueError:
        category = "profile_configuration"

    profile.status = "degraded"
    error = SystemError(
        tenant_id=profile.tenant_id,
        service="hermes_health",
        severity="high",
        message="Hermes profile health check failed",
        stack=None,
        error_metadata={"category": category, "profile_id": profile.id},
    )
    db.add(error)
    db.flush()
    source = AlertSource(
        event_type="hermes_health_failure",
        source_type="system_error",
        source_id=error.id,
        occurred_at=_as_utc(error.created_at),
        tenant_id=profile.tenant_id,
        category=category,
    )
    enqueue_alert_job(db, source, settings)
    return {"status": "alert_queued", "category": category}


def _build_envelope(source: AlertSource, secret: str) -> dict[str, object]:
    event_id = hashlib.sha256(
        f"{source.event_type}\0{source.source_type}\0{source.source_id}".encode()
    ).hexdigest()
    tenant_reference = (
        hmac.new(
            secret.encode("utf-8"), source.tenant_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:24]
        if source.tenant_id
        else None
    )
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": source.event_type,
        "severity": "high",
        "occurred_at": source.occurred_at.isoformat(),
        "service": _service_for(source.event_type),
        "summary": _summary_for(source.event_type),
        "attributes": {
            "category": source.category,
            "source_reference": hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()[:24],
            "tenant_reference": tenant_reference,
        },
    }


def _source_from_payload(payload: dict[str, Any]) -> AlertSource:
    event_type = payload.get("event_type")
    source_type = payload.get("source_type")
    source_id = payload.get("source_id")
    occurred_at = payload.get("occurred_at")
    tenant_id = payload.get("tenant_id")
    category = payload.get("category")
    if event_type not in ALERT_EVENT_TYPES:
        raise PermanentJobError("Operational alert event type is invalid")
    if not isinstance(source_type, str) or SAFE_TOKEN.fullmatch(source_type) is None:
        raise PermanentJobError("Operational alert source type is invalid")
    if not isinstance(source_id, str) or not 1 <= len(source_id) <= 160:
        raise PermanentJobError("Operational alert source is invalid")
    if tenant_id is not None and (not isinstance(tenant_id, str) or len(tenant_id) > 160):
        raise PermanentJobError("Operational alert tenant reference is invalid")
    if not isinstance(category, str):
        raise PermanentJobError("Operational alert category is invalid")
    try:
        parsed_at = datetime.fromisoformat(occurred_at) if isinstance(occurred_at, str) else None
    except ValueError as exc:
        raise PermanentJobError("Operational alert timestamp is invalid") from exc
    if parsed_at is None:
        raise PermanentJobError("Operational alert timestamp is invalid")
    return AlertSource(
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        occurred_at=_as_utc(parsed_at),
        tenant_id=tenant_id,
        category=_safe_category(category),
    )


def _safe_category(value: object) -> str:
    return value if isinstance(value, str) and SAFE_TOKEN.fullmatch(value) else "provider_failure"


def _service_for(event_type: AlertEventType) -> str:
    return {
        "bumpa_sync_failure": "bumpa",
        "whatsapp_delivery_failure": "whatsapp",
        "hermes_call_failure": "hermes",
        "hermes_health_failure": "hermes",
    }[event_type]


def _summary_for(event_type: AlertEventType) -> str:
    return {
        "bumpa_sync_failure": "Bumpa sync needs operator attention",
        "whatsapp_delivery_failure": "WhatsApp delivery needs operator attention",
        "hermes_call_failure": "Hermes call needs operator attention",
        "hermes_health_failure": "Hermes profile health needs operator attention",
    }[event_type]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _not_already_alerted(
    event_type: AlertEventType,
    source_type: str,
    source_id: Any,
) -> Any:
    prefix = f"ops-alert:{event_type}:{source_type}:"
    return ~exists(
        select(AsyncJob.id).where(
            AsyncJob.queue_name == "default",
            AsyncJob.idempotency_key == prefix + source_id,
        )
    )
