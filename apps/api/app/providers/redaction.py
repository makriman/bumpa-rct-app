from __future__ import annotations

import base64
import hashlib
import hmac
import re
from decimal import Decimal, InvalidOperation
from typing import Any

SENSITIVE_ORDER_FIELDS = {
    "customer_details",
    "shipping_details",
    "invoice_pdf",
    "customer_url",
    "order_page",
    "unique_hash",
    "proof_of_payment",
    "proof_urls",
    "shipping_slip",
}

# Research text may come from historic rows written before the latest redaction
# rules. Keep these patterns here as a reusable, defence-in-depth boundary for
# every researcher-facing read and export.
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
URL_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>{}\[\]\"']+")
WHATSAPP_MESSAGE_ID_RE = re.compile(r"(?i)(?<![A-Za-z0-9_])wamid\.[A-Za-z0-9_=+./-]{6,}")
LABELLED_WHATSAPP_ID_RE = re.compile(
    r"(?i)\b(?:whatsapp(?:\s+message)?\s+(?:id|identifier)|wa[_\s-]?id)"
    r"\s*(?:is\s+|[:=#-]\s*)[A-Za-z0-9_=+./-]{6,}"
)
LABELLED_ORDER_ID_RE = re.compile(
    r"(?i)\b(?:order|invoice)\s*(?:id|number|no\.?|#)"
    r"\s*(?:is\s+|[:=#-]\s*)?(?=[A-Za-z0-9._/-]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9._/-]{3,}"
)
LABELLED_NAME_RE = re.compile(
    r"(?i)\b(?P<label>(?:customer|buyer|recipient|contact|client)"
    r"(?:\s+full)?\s+name)\s*(?:is\s+|[:=~-]\s*)"
    r"(?P<value>[^\n;,|.!?]{2,80})"
)
LABELLED_ADDRESS_RE = re.compile(
    r"(?i)\b(?P<label>(?:(?:customer|shipping|delivery|billing|home|office)\s+)?address)"
    r"\s*(?:is\s+|[:=~-]\s*)(?P<value>[^\n;|.!?]{3,160})"
)
DELIVERY_ADDRESS_RE = re.compile(
    r"(?i)\b(?P<label>(?:deliver|ship|send)(?:\s+(?:this|it|the\s+order))?\s+to)\s+"
    r"(?P<value>\d{1,6}[^\n;|.!?]{2,150})"
)
ADDRESS_SIGNAL_RE = re.compile(
    r"(?i)\b(?:street|st|road|rd|close|avenue|ave|way|estate|phase|plot|lane|"
    r"drive|crescent|court|junction|lekki|ikeja|lagos|abuja)\b"
)
FORMULA_PREFIX_RE = re.compile(r"^[\s\x00-\x1f]*[=+\-@]")

SENSITIVE_STRUCTURED_FIELDS = SENSITIVE_ORDER_FIELDS | {
    "address",
    "billing_address",
    "buyer",
    "buyer_details",
    "customer",
    "customer_address",
    "customer_email",
    "customer_id",
    "customer_name",
    "customer_phone",
    "delivery_address",
    "delivery_details",
    "email",
    "invoice_url",
    "order_url",
    "payment_url",
    "phone",
    "phone_e164",
    "phone_number",
    "recipient",
    "recipient_address",
    "recipient_name",
    "recipient_phone",
    "shipping_address",
    "wa_id",
    "whatsapp_message_id",
}

PSEUDONYM_LABELS = {
    "tenant": "SME",
    "user": "USR",
    "event": "EVT",
    "conversation": "CONV",
}


def parse_money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.replace("₦", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _redact_structured(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _normalise_key(str(key)) in SENSITIVE_STRUCTURED_FIELDS
                else _redact_structured(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_structured(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_structured(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deep, non-mutating research-safe copy of a raw order payload."""

    redacted = _redact_structured(payload)
    if not isinstance(redacted, dict):  # pragma: no cover - retained for type safety
        raise TypeError("Order payload must be a mapping")
    return redacted


def _redact_labelled_address(match: re.Match[str]) -> str:
    label = match.group("label")
    value = match.group("value")
    # A bare "address" can be used as a verb (for example, "address: falling
    # sales"). Require postal evidence in that ambiguous case; qualified labels
    # such as "shipping address" are unambiguously sensitive.
    if label.lower() == "address" and not ADDRESS_SIGNAL_RE.search(value):
        return match.group(0)
    return f"{label}: [ADDRESS]"


def _redact_delivery_address(match: re.Match[str]) -> str:
    if not ADDRESS_SIGNAL_RE.search(match.group("value")):
        return match.group(0)
    return f"{match.group('label')} [ADDRESS]"


def _redact_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    # ITU E.164 numbers have at most 15 digits. Requiring at least 10 prevents
    # dates and short commerce references from being erased as false positives.
    return "[PHONE]" if 10 <= len(digits) <= 15 else match.group(0)


def redact_text(value: str) -> str:
    """Redact reliably identifiable PII without erasing ordinary business language."""

    redacted = WHATSAPP_MESSAGE_ID_RE.sub("[WHATSAPP_ID]", value)
    redacted = LABELLED_WHATSAPP_ID_RE.sub("WhatsApp ID: [WHATSAPP_ID]", redacted)
    redacted = URL_RE.sub("[URL]", redacted)
    redacted = EMAIL_RE.sub("[EMAIL]", redacted)
    redacted = PHONE_RE.sub(_redact_phone, redacted)
    redacted = LABELLED_ORDER_ID_RE.sub("Order ID: [ORDER_ID]", redacted)
    redacted = LABELLED_NAME_RE.sub(lambda match: f"{match.group('label')}: [NAME]", redacted)
    redacted = LABELLED_ADDRESS_RE.sub(_redact_labelled_address, redacted)
    return DELIVERY_ADDRESS_RE.sub(_redact_delivery_address, redacted)


def pseudonymize(identifier: str | None, secret: str, *, namespace: str) -> str:
    """Create a deterministic, domain-separated pseudonym without exposing an ID hash."""

    label = PSEUDONYM_LABELS.get(namespace, "REF")
    if not identifier:
        return f"{label}-UNKNOWN"
    key = hashlib.sha256(f"bumpabestie:pseudonym:v1:{secret}".encode()).digest()
    digest = hmac.new(key, f"{namespace}\0{identifier}".encode(), hashlib.sha256).digest()
    token = base64.b32encode(digest[:10]).decode().rstrip("=")
    return f"{label}-{token}"


def csv_safe(value: Any) -> Any:
    if isinstance(value, str) and FORMULA_PREFIX_RE.match(value):
        return "'" + value
    return value
