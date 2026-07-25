from __future__ import annotations

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import HermesProfile, Tenant
from app.db.session import SessionLocal, set_security_context
from app.providers.hermes import reconcile_profile_bundle


def main() -> None:
    settings = get_settings()
    reconciled = 0
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        rows = db.execute(
            select(HermesProfile, Tenant)
            .join(Tenant, Tenant.id == HermesProfile.tenant_id)
            .where(HermesProfile.provider == "hermes")
            .order_by(HermesProfile.id)
        ).all()
        for profile, tenant in rows:
            reconcile_profile_bundle(profile, tenant, settings)
            reconciled += 1
        db.commit()
    print(reconciled)


if __name__ == "__main__":
    main()
