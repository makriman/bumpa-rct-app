from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


def validate_store_timezone(value: str) -> str:
    """Return a validated IANA timezone name without silently substituting one."""

    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 64:
        raise ValueError("Store timezone must be a normalized IANA timezone name")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Store timezone must be a valid IANA timezone name") from exc
    return value


def validate_store_currency(value: str) -> str:
    """Return a normalized three-letter store currency code."""

    if not isinstance(value, str) or value != value.strip():
        raise ValueError("Store currency must be normalized")
    normalized = value.upper()
    if _CURRENCY_CODE.fullmatch(normalized) is None:
        raise ValueError("Store currency must be a three-letter code")
    return normalized
