from datetime import datetime

import pytest

from app.providers.redaction import redact_order_payload


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-24 00:00:00",
        "2026-11-24T21:47:28.000000Z",
        "2026-11-24",
        "2026-11-24T01:02:03+01:00",
    ],
)
def test_order_evidence_retains_valid_dates_without_retaining_customer_pii(value):
    original = {
        "order_date": value,
        "created_at": value,
        "customer_details": {"phone": "+2348091234567"},
        "notes": "Customer phone +2348091234567, email customer@example.com",
    }
    safe = redact_order_payload(original)
    assert safe["order_date"] == value and safe["created_at"] == value
    datetime.fromisoformat(safe["order_date"].replace("Z", "+00:00"))
    assert safe["customer_details"] == "[REDACTED]"
    assert "2348091234567" not in safe["notes"] and "customer@example.com" not in safe["notes"]
    assert original["customer_details"]["phone"] == "+2348091234567"


@pytest.mark.parametrize(
    "value", ["+2348091234567", "customer@example.com", "2026-99-99 00:00:00 +2348091234567"]
)
def test_date_field_name_does_not_bypass_redaction_for_arbitrary_strings(value):
    assert redact_order_payload({"order_date": value})["order_date"] != value
