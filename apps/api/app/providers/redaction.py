from __future__ import annotations

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
PHONE_RE = re.compile(r"\+?\d[\d\s()-]{7,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


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


def redact_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "[REDACTED]" if key in SENSITIVE_ORDER_FIELDS else value
        for key, value in payload.items()
    }


def redact_text(value: str) -> str:
    return EMAIL_RE.sub("[EMAIL]", PHONE_RE.sub("[PHONE]", value))


def csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
