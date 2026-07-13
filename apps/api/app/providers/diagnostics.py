from __future__ import annotations

import hashlib
import re
from typing import Literal
from uuid import UUID

ProviderFailureCategory = Literal[
    "authentication",
    "invalid_response",
    "provider",
    "rate_limited",
    "response_too_large",
    "timeout",
    "transport",
]
ProviderName = Literal["bumpa", "meta"]
ProviderOperation = Literal["otp_delivery", "sync"]

PROVIDER_FAILURE_CATEGORIES = frozenset(
    {
        "authentication",
        "invalid_response",
        "provider",
        "rate_limited",
        "response_too_large",
        "timeout",
        "transport",
    }
)
PROVIDER_NAMES = frozenset({"bumpa", "meta"})
PROVIDER_OPERATIONS = frozenset({"otp_delivery", "sync"})
MAX_PROVIDER_REQUEST_ID_BYTES = 512
MAX_PROVIDER_CODE = 2_147_483_647
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
NUMERIC_CODE_RE = re.compile(r"^[0-9]{1,10}$")
PROVIDER_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/=+\-]{7,255}$")
SENSITIVE_DIGIT_SEQUENCE_RE = re.compile(r"[0-9]{6,15}")


def provider_request_id_hash(value: str | None) -> str | None:
    """Return a bounded, non-reversible correlation value for an upstream ID.

    Provider headers are still untrusted input. Logging their raw value would let
    an upstream or intermediary place a phone, OTP, credential, or response detail
    in an otherwise allowlisted field. A full SHA-256 value remains searchable when
    an operator already has the provider request ID without exposing that ID itself.
    """

    if not isinstance(value, str) or not PROVIDER_REQUEST_ID_RE.fullmatch(value):
        return None
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_PROVIDER_REQUEST_ID_BYTES:
        return None
    # A plain digest is appropriate only for an opaque, high-entropy provider ID.
    # Short numeric identifiers and values containing OTP/phone-length digit runs
    # are cheaply enumerable and therefore must be omitted rather than hashed.
    if not any(character.isalpha() for character in value):
        return None
    if SENSITIVE_DIGIT_SEQUENCE_RE.search(value):
        return None
    return hashlib.sha256(encoded).hexdigest()


def normalise_provider_code(value: object) -> str | None:
    """Accept only a bounded provider error number, never arbitrary body text."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and NUMERIC_CODE_RE.fullmatch(value):
        parsed = int(value)
    else:
        return None
    if not 0 <= parsed <= MAX_PROVIDER_CODE:
        return None
    return str(parsed)


def provider_failure_log_extra(
    *,
    provider: ProviderName,
    operation: ProviderOperation,
    category: ProviderFailureCategory,
    retryable: bool,
    http_status: int | None = None,
    code: object = None,
    request_id_hash: str | None = None,
    retry_after_seconds: int | float | None = None,
    sync_run_id: str | None = None,
) -> dict[str, object]:
    """Build the only structured fields permitted for provider failure logs."""

    if provider not in PROVIDER_NAMES:
        raise ValueError("Unsupported provider diagnostic")
    if operation not in PROVIDER_OPERATIONS:
        raise ValueError("Unsupported provider operation diagnostic")
    if category not in PROVIDER_FAILURE_CATEGORIES:
        raise ValueError("Unsupported provider failure diagnostic")
    if not isinstance(retryable, bool):
        raise TypeError("Provider retryability must be boolean")

    extra: dict[str, object] = {
        "provider": provider,
        "provider_operation": operation,
        "provider_category": category,
        "provider_retryable": retryable,
    }
    if (
        isinstance(http_status, int)
        and not isinstance(http_status, bool)
        and 100 <= http_status <= 599
    ):
        extra["provider_http_status"] = http_status
    if (safe_code := normalise_provider_code(code)) is not None:
        extra["provider_code"] = safe_code
    if isinstance(request_id_hash, str) and SHA256_RE.fullmatch(request_id_hash):
        extra["provider_request_id_hash"] = request_id_hash
    if (
        isinstance(retry_after_seconds, (int, float))
        and not isinstance(retry_after_seconds, bool)
        and 0 <= retry_after_seconds <= 86_400
    ):
        extra["retry_after_seconds"] = retry_after_seconds
    if isinstance(sync_run_id, str):
        try:
            canonical_run_id = str(UUID(sync_run_id))
        except ValueError:
            canonical_run_id = None
        if canonical_run_id == sync_run_id:
            extra["sync_run_id"] = sync_run_id
    return extra
