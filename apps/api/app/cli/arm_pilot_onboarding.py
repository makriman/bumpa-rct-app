"""Apply the owner's captured existing-pilot default, without sends or enrollment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any
from uuid import UUID


def validate_snapshot(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict) or value.get("cohort") != "existing-pilot-2026-10-24":
        raise ValueError("Expected the explicitly captured existing-pilot cohort")
    rows = value.get("identities")
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 100
        or value.get("identity_count") != len(rows)
    ):
        raise ValueError("Invalid bounded pilot snapshot")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "identity_id",
            "tenant_id",
            "user_id",
            "role",
            "phone_fingerprint",
        }:
            raise ValueError("Pilot snapshot must contain IDs and fingerprints only")
        for key in ("identity_id", "tenant_id", "user_id"):
            if not isinstance(row[key], str) or str(UUID(row[key])) != row[key]:
                raise ValueError("Invalid pilot identity reference")
        if row["identity_id"] in seen or row["role"] not in {"owner", "admin", "member"}:
            raise ValueError("Duplicate or invalid pilot mapping")
        if not isinstance(row["phone_fingerprint"], str) or not re.fullmatch(
            r"[a-f0-9]{64}", row["phone_fingerprint"]
        ):
            raise ValueError("Invalid pilot fingerprint")
        seen.add(row["identity_id"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = validate_snapshot(json.load(sys.stdin))
    if not args.apply:
        print(json.dumps({"captured": len(rows), "validated_only": True, "messages_sent": 0}))
        return
    from app.core.config import get_settings
    from app.db.session import SessionLocal, set_security_context
    from app.services.business_onboarding import arm_pilot_inbound_defaults

    settings = get_settings()
    if (
        settings.whatsapp_backend != "disabled"
        or settings.agent_backend != "disabled"
        or settings.bumpa_backend != "disabled"
    ):
        raise RuntimeError("Use the network-limited migration service with providers disabled")
    with SessionLocal.begin() as db:
        set_security_context(db, privileged=True)
        result = arm_pilot_inbound_defaults(db, rows, settings.otp_secret)
    print(json.dumps({**result, "messages_sent": 0, "enrollments_created": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
