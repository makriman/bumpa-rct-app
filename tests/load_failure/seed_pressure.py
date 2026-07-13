#!/usr/bin/env python3
"""Seed two synthetic tenants and emit short-lived auth material for the pressure drill."""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.crypto import FieldCipher
from app.core.security import create_access_token
from app.db.models import (
    BumpaConnection,
    HermesProfile,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal, set_security_context
from sqlalchemy import select

RUN_ID = re.compile(r"^[0-9a-f]{12}$")


def main() -> int:
    if len(sys.argv) != 2 or not RUN_ID.fullmatch(sys.argv[1]):
        raise SystemExit("pressure fixture requires one 12-character lowercase hex run ID")
    run_id = sys.argv[1]
    settings = get_settings()
    if not (
        os.environ.get("LOAD_FAILURE_FIXTURE_MODE") == "true"
        and urlsplit(settings.database_url).hostname == "postgres"
        and settings.redis_url.startswith("redis://redis:")
        and settings.app_env == "staging"
        and settings.whatsapp_backend == "mock"
        and settings.agent_backend == "mock"
        and settings.bumpa_backend == "mock"
    ):
        raise SystemExit("refusing to seed outside the isolated synthetic-provider stack")
    cipher = FieldCipher.from_settings(settings)

    fixtures: dict[str, dict[str, str]] = {}
    numeric_suffix = int(run_id, 16) % 10_000_000
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        for index, side in enumerate(("a", "b")):
            tenant_id = f"lf-pressure-{side}-{run_id}"
            user_id = f"lf-user-{side}-{run_id}"
            phone = f"+1556{index}{numeric_suffix:07d}"
            if db.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is not None:
                raise SystemExit("pressure fixture already exists")
            tenant = Tenant(
                id=tenant_id,
                slug=f"lf-pressure-{side}-{run_id}",
                name=f"Synthetic pressure tenant {side.upper()}",
                status="active",
                timezone="UTC",
                currency_code="NGN",
                research_consent_status="withdrawn",
            )
            user = User(
                id=user_id,
                name=f"Synthetic pressure owner {side.upper()}",
                primary_phone_e164=phone,
                status="active",
            )
            db.add_all((tenant, user))
            db.flush()
            db.add_all(
                (
                    TenantMembership(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        role="owner",
                        status="active",
                    ),
                    PhoneIdentity(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        phone_e164=phone,
                        status="approved",
                        opt_out=False,
                    ),
                    BumpaConnection(
                        tenant_id=tenant_id,
                        encrypted_api_key=cipher.encrypt(
                            "synthetic-local-provider-has-no-credential"
                        ),
                        scope_type="business_id",
                        scope_id=f"synthetic-{side}-{run_id}",
                        provider="local",
                        status="active",
                    ),
                    HermesProfile(
                        tenant_id=tenant_id,
                        profile_name=f"lf_pressure_{side}_{run_id}",
                        provider="local",
                        api_internal_url="local://agent",
                        encrypted_api_key=cipher.encrypt("synthetic-local-agent-has-no-credential"),
                        status="active",
                    ),
                )
            )
            db.flush()
            token, _session = create_access_token(db, user, settings)
            fixtures[side] = {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "token": token,
            }

    print(json.dumps(fixtures, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
