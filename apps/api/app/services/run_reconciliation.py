"""Read-only decisions for interrupted application runs.

The database owns claiming, fencing and applying these decisions. A caller must
claim/revoke the old execution capability before taking a snapshot, then apply a
decision only while it still owns that claim. This module never submits, stops,
sends or retries an external effect. In particular, absence of an admission ID
does not establish that a provider request was never admitted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

_PROVIDER_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
_CITATION_URL = re.compile(r"https://[^\s)>\]]+")
_MAX_OUTPUT_CHARS = 131_072


class ModelOutcome(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class DeliveryOutcome(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReconciliationAction(StrEnum):
    WAIT = "wait"
    POLL_LATER = "poll_later"
    PERSIST_OUTPUT = "persist_output"
    RESUME_DELIVERY = "resume_delivery"
    COMPLETE = "complete"
    FAIL = "fail"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class FrozenRequest:
    """Immutable exact request envelope, excluding transport credentials.

    Freeze the actual canonical provider payload, profile/endpoint binding,
    idempotency key and application output policy before any admission attempt.
    A digest of reconstructed current context is not an equivalent snapshot.
    """

    payload_json: str = field(repr=False)
    sha256: str

    def is_valid(self) -> bool:
        if not isinstance(self.payload_json, str) or not isinstance(self.sha256, str):
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            return False
        try:
            payload = json.loads(self.payload_json)
            if not isinstance(payload, dict):
                return False
            canonical = _canonical_json(payload)
        except (TypeError, ValueError):
            return False
        return (
            canonical == self.payload_json
            and hashlib.sha256(canonical.encode()).hexdigest() == self.sha256
        )


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def freeze_request(payload: Mapping[str, object]) -> FrozenRequest:
    """Detach JSON data from mutable caller state and retain an integrity digest."""

    canonical = _canonical_json(payload)
    return FrozenRequest(canonical, hashlib.sha256(canonical.encode()).hexdigest())


@dataclass(frozen=True, slots=True)
class DurableModelOutput:
    """An application-accepted outbound linked exactly to this initiating turn."""

    message_id: str
    run_id: str
    initiating_message_id: str
    content: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    effect_key: str
    status: str
    provider_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    initiating_message_id: str
    status: str
    expires_at: datetime
    provider_run_ids: tuple[str, ...] = ()
    request_snapshot: FrozenRequest | None = None
    durable_output: DurableModelOutput | None = None
    delivery_required: bool = True
    # None means delivery has not been planned. A nonempty immutable manifest is
    # required before existing delivery receipts can establish logical success.
    delivery_effect_keys: tuple[str, ...] | None = None
    deliveries: tuple[DeliveryReceipt, ...] = ()
    minimum_citations: int = 0


@dataclass(frozen=True, slots=True)
class RecoveredModelOutput:
    provider_run_id: str
    content: str = field(repr=False)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    provider_run_id: str
    outcome: ModelOutcome
    reason: str


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    action: ReconciliationAction
    model_outcome: ModelOutcome
    delivery_outcome: DeliveryOutcome
    reason: str
    output: RecoveredModelOutput | None = None
    observations: tuple[ProviderObservation, ...] = ()


def delivery_outcome(snapshot: RunSnapshot) -> DeliveryOutcome:
    """Classify a whole frozen effect manifest without assuming a send outcome."""

    receipts = snapshot.deliveries
    keys = snapshot.delivery_effect_keys
    if not snapshot.delivery_required:
        return DeliveryOutcome.UNKNOWN if receipts or keys else DeliveryOutcome.NOT_REQUIRED
    if keys is None:
        return DeliveryOutcome.UNKNOWN if receipts else DeliveryOutcome.PENDING
    if not keys or len(set(keys)) != len(keys) or any(not key for key in keys):
        return DeliveryOutcome.UNKNOWN
    seen: dict[str, DeliveryReceipt] = {}
    for item in receipts:
        if item.effect_key not in keys or item.effect_key in seen:
            return DeliveryOutcome.UNKNOWN
        seen[item.effect_key] = item
    outcomes: list[DeliveryOutcome] = []
    for key in keys:
        receipt = seen.get(key)
        if receipt is None:
            outcomes.append(DeliveryOutcome.PENDING)
        elif receipt.status in {"sent", "delivered", "read"}:
            outcomes.append(
                DeliveryOutcome.COMPLETED
                if isinstance(receipt.provider_message_id, str) and receipt.provider_message_id
                else DeliveryOutcome.UNKNOWN
            )
        elif receipt.provider_message_id:
            # A durable provider identity proves submission. Never infer that a
            # "failed" delivery notification makes repeating that send safe.
            outcomes.append(DeliveryOutcome.UNKNOWN)
        elif receipt.status in {"prepared", "failed"}:
            outcomes.append(DeliveryOutcome.PENDING)
        elif receipt.status == "rejected":
            outcomes.append(DeliveryOutcome.FAILED)
        else:
            # Includes the crash window after persisting "sending", timeouts,
            # transport errors and unfamiliar future states.
            outcomes.append(DeliveryOutcome.UNKNOWN)
    if DeliveryOutcome.UNKNOWN in outcomes:
        return DeliveryOutcome.UNKNOWN
    if DeliveryOutcome.FAILED in outcomes:
        return DeliveryOutcome.FAILED
    if DeliveryOutcome.PENDING in outcomes:
        return DeliveryOutcome.PENDING
    return DeliveryOutcome.COMPLETED


def reconcile_run(
    snapshot: RunSnapshot,
    *,
    now: datetime,
    poll: Callable[[str], object],
) -> ReconciliationDecision:
    """Poll only recorded admissions and return a bounded, immutable decision.

    ``poll`` must be a read-only lookup in the persisted run's tenant/profile
    endpoint. It receives a validated recorded ID, never a reconstructed one.
    The ordered admissions may include an internal citation repair; only the
    final admission can supply an answer, and preceding admissions must be
    terminal. Output persistence must be idempotent on the initiating turn/run.
    A recovered model result never establishes that delivery succeeded.
    """

    delivery = delivery_outcome(snapshot)

    def decide(
        action: ReconciliationAction,
        model: ModelOutcome,
        reason: str,
        output: RecoveredModelOutput | None = None,
        observations: tuple[ProviderObservation, ...] = (),
    ) -> ReconciliationDecision:
        return ReconciliationDecision(action, model, delivery, reason, output, observations)

    if (
        not snapshot.run_id
        or not snapshot.initiating_message_id
        or snapshot.status not in {"running", "reconciling", "completed", "failed", "expired"}
        or not isinstance(snapshot.minimum_citations, int)
        or isinstance(snapshot.minimum_citations, bool)
        or not 0 <= snapshot.minimum_citations <= 10
    ):
        return decide(ReconciliationAction.REVIEW, ModelOutcome.UNKNOWN, "invalid_run_snapshot")
    if snapshot.status == "running" and _as_utc(snapshot.expires_at) > _as_utc(now):
        return decide(ReconciliationAction.WAIT, ModelOutcome.PENDING, "active_run_not_expired")

    durable = snapshot.durable_output
    if durable is not None:
        if (
            not durable.message_id
            or durable.run_id != snapshot.run_id
            or durable.initiating_message_id != snapshot.initiating_message_id
            or not isinstance(durable.content, str)
            or not durable.content.strip()
        ):
            return decide(
                ReconciliationAction.REVIEW, ModelOutcome.UNKNOWN, "durable_output_binding_invalid"
            )
        if delivery in {DeliveryOutcome.COMPLETED, DeliveryOutcome.NOT_REQUIRED}:
            return decide(
                ReconciliationAction.COMPLETE, ModelOutcome.COMPLETED, "durable_output_and_delivery"
            )
        if delivery == DeliveryOutcome.PENDING:
            return decide(
                ReconciliationAction.RESUME_DELIVERY, ModelOutcome.COMPLETED, "durable_output_only"
            )
        return decide(
            ReconciliationAction.REVIEW,
            ModelOutcome.COMPLETED,
            "delivery_failed" if delivery == DeliveryOutcome.FAILED else "delivery_outcome_unknown",
        )

    ids = snapshot.provider_run_ids
    if not ids:
        return decide(
            ReconciliationAction.REVIEW, ModelOutcome.UNKNOWN, "provider_admission_unknown"
        )
    if any(not isinstance(run_id, str) or not _PROVIDER_ID.fullmatch(run_id) for run_id in ids):
        return decide(
            ReconciliationAction.REVIEW, ModelOutcome.UNKNOWN, "provider_identity_invalid"
        )
    if len(set(ids)) != len(ids):
        return decide(
            ReconciliationAction.REVIEW, ModelOutcome.UNKNOWN, "provider_admission_duplicate"
        )

    observations: list[ProviderObservation] = []
    completed_outputs: list[RecoveredModelOutput] = []
    final_output: RecoveredModelOutput | None = None
    for run_id in ids:
        try:
            payload = poll(run_id)
        except Exception:
            # Exception text and upstream bodies may contain credentials or
            # merchant data. Persist only this safe status code.
            observation = ProviderObservation(run_id, ModelOutcome.UNKNOWN, "poll_unavailable")
            output = None
        else:
            observation, output = _observe(run_id, payload)
        observations.append(observation)
        if output is not None:
            completed_outputs.append(output)
        final_output = output
    observed = tuple(observations)
    final = observed[-1]
    if any(item.outcome != ModelOutcome.COMPLETED for item in observed[:-1]):
        return decide(
            ReconciliationAction.REVIEW,
            ModelOutcome.UNKNOWN,
            "preceding_provider_outcome_unresolved",
            observations=observed,
        )
    if final.outcome == ModelOutcome.UNKNOWN:
        return decide(
            ReconciliationAction.POLL_LATER
            if final.reason == "poll_unavailable"
            else ReconciliationAction.REVIEW,
            ModelOutcome.UNKNOWN,
            final.reason,
            observations=observed,
        )
    if final.outcome == ModelOutcome.PENDING:
        return decide(
            ReconciliationAction.POLL_LATER,
            ModelOutcome.PENDING,
            "provider_still_running",
            observations=observed,
        )
    if final.outcome == ModelOutcome.FAILED:
        return decide(
            ReconciliationAction.REVIEW
            if delivery == DeliveryOutcome.UNKNOWN
            else ReconciliationAction.FAIL,
            ModelOutcome.FAILED,
            "provider_failed",
            observations=observed,
        )
    if snapshot.request_snapshot is None or not snapshot.request_snapshot.is_valid():
        return decide(
            ReconciliationAction.REVIEW,
            ModelOutcome.COMPLETED,
            "frozen_request_missing_or_invalid",
            observations=observed,
        )
    if final_output is None or _citation_count(final_output.content) < snapshot.minimum_citations:
        return decide(
            ReconciliationAction.REVIEW,
            ModelOutcome.COMPLETED,
            "output_validation_failed",
            observations=observed,
        )
    # The canonical adapter charges the draft plus its internal repair to one
    # application turn. Preserve that accounting when recovering the final text.
    final_output = replace(
        final_output,
        input_tokens=sum(output.input_tokens for output in completed_outputs),
        output_tokens=sum(output.output_tokens for output in completed_outputs),
        total_tokens=sum(output.total_tokens for output in completed_outputs),
    )
    return decide(
        ReconciliationAction.PERSIST_OUTPUT,
        ModelOutcome.COMPLETED,
        "provider_output_recovered",
        final_output,
        observed,
    )


def _observe(
    run_id: str, payload: object
) -> tuple[ProviderObservation, RecoveredModelOutput | None]:
    def observed(
        outcome: ModelOutcome, reason: str, output: RecoveredModelOutput | None = None
    ) -> tuple[ProviderObservation, RecoveredModelOutput | None]:
        return ProviderObservation(run_id, outcome, reason), output

    if not isinstance(payload, Mapping):
        return observed(ModelOutcome.UNKNOWN, "provider_response_invalid")
    if payload.get("run_id") != run_id:
        return observed(ModelOutcome.UNKNOWN, "provider_identity_mismatch")
    status = payload.get("status")
    if not isinstance(status, str):
        return observed(ModelOutcome.UNKNOWN, "provider_status_unknown")
    if status in {"queued", "running"}:
        return observed(ModelOutcome.PENDING, "provider_still_running")
    if status in {"failed", "cancelled"}:
        return observed(ModelOutcome.FAILED, "provider_failed")
    if status != "completed":
        return observed(ModelOutcome.UNKNOWN, "provider_status_unknown")
    content = payload.get("output")
    usage = payload.get("usage", {})
    session = payload.get("session_id")
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content) > _MAX_OUTPUT_CHARS
        or not isinstance(usage, Mapping)
        or (session is not None and (not isinstance(session, str) or len(session) > 160))
    ):
        return observed(ModelOutcome.UNKNOWN, "provider_output_invalid")
    input_tokens = _safe_token_count(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _safe_token_count(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _safe_token_count(usage.get("total_tokens")) or input_tokens + output_tokens
    return observed(
        ModelOutcome.COMPLETED,
        "provider_completed",
        RecoveredModelOutput(
            run_id, content.strip(), input_tokens, output_tokens, total_tokens, session
        ),
    )


def _safe_token_count(value: object) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000_000
        else 0
    )


def _citation_count(content: str) -> int:
    return len({url.rstrip(".,;:'\"") for url in _CITATION_URL.findall(content)})


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
