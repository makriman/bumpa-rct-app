from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.db.models import (
    BumpaConnection,
    HermesProfile,
    PhoneIdentity,
    PlatformRole,
    ResearchConsent,
    Tenant,
    TenantMembership,
    User,
)

DEMO_USERS = {
    "owner": ("Ada Owner", "+2348012345678", "ada@example.test"),
    "other_owner": ("Bola Owner", "+2348012345679", "bola@example.test"),
    "operator": ("Ope Operator", "+2348099990001", "operator@example.test"),
    "researcher": ("Remi Researcher", "+2348099990002", "researcher@example.test"),
    "superadmin": ("Sam Superadmin", "+2348099990000", "admin@example.test"),
}


def seed_demo(db: Session, settings: Settings) -> None:
    if db.scalar(select(Tenant.id).limit(1)):
        return
    cipher = FieldCipher.from_settings(settings)
    demo = Tenant(
        slug="demo-store",
        name="Ada's Demo Store",
        business_category="Retail",
        country="NG",
        city="Lagos",
        research_consent_status="granted",
    )
    other = Tenant(
        slug="other-store",
        name="Other Isolation Store",
        business_category="Fashion",
        country="NG",
        city="Abuja",
        research_consent_status="granted",
    )
    db.add_all([demo, other])
    db.flush()
    users: dict[str, User] = {}
    for key, (name, phone, email) in DEMO_USERS.items():
        users[key] = User(name=name, primary_phone_e164=phone, email=email)
        db.add(users[key])
    db.flush()
    db.add_all(
        [
            TenantMembership(tenant_id=demo.id, user_id=users["owner"].id, role="owner"),
            TenantMembership(tenant_id=other.id, user_id=users["other_owner"].id, role="owner"),
            PhoneIdentity(
                tenant_id=demo.id,
                user_id=users["owner"].id,
                phone_e164=users["owner"].primary_phone_e164,
            ),
            PhoneIdentity(
                tenant_id=other.id,
                user_id=users["other_owner"].id,
                phone_e164=users["other_owner"].primary_phone_e164,
            ),
            PlatformRole(user_id=users["operator"].id, role="operator"),
            PlatformRole(user_id=users["researcher"].id, role="researcher"),
            PlatformRole(user_id=users["superadmin"].id, role="superadmin"),
            PlatformRole(user_id=users["superadmin"].id, role="operator"),
            PlatformRole(user_id=users["superadmin"].id, role="researcher"),
            ResearchConsent(tenant_id=demo.id, status="granted", actor_user_id=users["owner"].id),
            ResearchConsent(
                tenant_id=other.id, status="granted", actor_user_id=users["other_owner"].id
            ),
            BumpaConnection(
                tenant_id=demo.id,
                encrypted_api_key=cipher.encrypt("local-demo-bumpa-key"),
                scope_type="business_id",
                scope_id="demo-business",
                provider="local",
            ),
            BumpaConnection(
                tenant_id=other.id,
                encrypted_api_key=cipher.encrypt("local-other-bumpa-key"),
                scope_type="business_id",
                scope_id="other-business",
                provider="local",
            ),
            HermesProfile(
                tenant_id=demo.id,
                profile_name=f"tenant_demo_store_{demo.id[:8]}",
                encrypted_api_key=cipher.encrypt("local-demo-agent-key"),
            ),
            HermesProfile(
                tenant_id=other.id,
                profile_name=f"tenant_other_store_{other.id[:8]}",
                encrypted_api_key=cipher.encrypt("local-other-agent-key"),
            ),
        ]
    )
    db.commit()
