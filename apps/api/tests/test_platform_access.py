from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import (
    AuditLog,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal, set_security_context
from app.main import app
from tests.conftest import auth_headers


def test_platform_access_directory_and_mutations_are_least_privilege_and_idempotent(
    client: TestClient,
) -> None:
    operator_headers = auth_headers(client, "+2348099990001")
    assert client.get("/v1/admin/platform-access", headers=operator_headers).status_code == 403

    superadmin_headers = auth_headers(client, "+2348099990000")
    directory = client.get("/v1/admin/platform-access", headers=superadmin_headers)
    assert directory.status_code == 200, directory.text
    rows = directory.json()
    actor = next(row for row in rows if row["phone_e164"] == "+2348099990000")
    owner = next(row for row in rows if row["phone_e164"] == "+2348012345678")
    researcher = next(row for row in rows if row["phone_e164"] == "+2348099990002")
    assert owner["platform_roles"] == []
    assert owner["has_active_mapping"] is True
    assert researcher["platform_roles"] == ["researcher"]
    assert researcher["has_active_mapping"] is False

    for role in ("operator", "researcher"):
        granted = client.put(
            f"/v1/admin/platform-access/{owner['user_id']}/{role}",
            headers=superadmin_headers,
        )
        assert granted.status_code == 200, granted.text
        assert role in granted.json()["platform_roles"]
        assert granted.json()["has_active_mapping"] is True

        repeated = client.put(
            f"/v1/admin/platform-access/{owner['user_id']}/{role}",
            headers=superadmin_headers,
        )
        assert repeated.status_code == 200, repeated.text

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        for role in ("operator", "researcher"):
            audit_rows = db.scalars(
                select(AuditLog).where(
                    AuditLog.action == f"platform.{role}.granted",
                    AuditLog.resource_type == "platform_role",
                    AuditLog.actor_user_id.is_not(None),
                )
            ).all()
            owner_audits = [
                row
                for row in audit_rows
                if row.after == {"user_id": owner["user_id"], "role": role}
            ]
            assert len(owner_audits) == 1
            assert owner_audits[0].actor_user_id == actor["user_id"]
            assert owner["phone_e164"] not in str(owner_audits[0].after)

    for role in ("researcher", "operator"):
        revoked = client.delete(
            f"/v1/admin/platform-access/{owner['user_id']}/{role}",
            headers=superadmin_headers,
        )
        assert revoked.status_code == 204, revoked.text
        repeated = client.delete(
            f"/v1/admin/platform-access/{owner['user_id']}/{role}",
            headers=superadmin_headers,
        )
        assert repeated.status_code == 204, repeated.text

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        remaining = db.scalar(
            select(func.count())
            .select_from(PlatformRole)
            .where(
                PlatformRole.user_id == owner["user_id"],
                PlatformRole.role.in_(("operator", "researcher")),
            )
        )
        assert remaining == 0
        for role in ("operator", "researcher"):
            revoke_count = db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == f"platform.{role}.revoked",
                    AuditLog.before == {"user_id": owner["user_id"], "role": role},
                )
            )
            assert revoke_count == 1


def test_platform_access_protects_superadmins_and_rejects_invalid_targets(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "+2348099990000")
    directory = client.get("/v1/admin/platform-access", headers=headers).json()
    superadmin = next(row for row in directory if "superadmin" in row["platform_roles"])

    for method in (client.put, client.delete):
        response = method(
            f"/v1/admin/platform-access/{superadmin['user_id']}/researcher",
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Superadministrator access is protected"

    assert (
        client.put(
            "/v1/admin/platform-access/not-a-user/operator",
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/v1/admin/platform-access/{superadmin['user_id']}/superadmin",
            headers=headers,
        ).status_code
        == 422
    )

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        owner = db.scalar(select(User).where(User.primary_phone_e164 == "+2348012345678"))
        assert owner is not None
        owner.status = "suspended"
        db.commit()
        owner_id = owner.id

    inactive = client.put(
        f"/v1/admin/platform-access/{owner_id}/operator",
        headers=headers,
    )
    assert inactive.status_code == 409
    assert inactive.json()["detail"] == "User is not active"

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        owner = db.get(User, owner_id)
        assert owner is not None
        owner.status = "active"
        db.commit()


def test_legacy_operator_revoke_cannot_mutate_another_superadmin(client: TestClient) -> None:
    headers = auth_headers(client, "+2348099990000")
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        target = User(
            name="Protected second superadmin",
            primary_phone_e164="+2348555555599",
            status="active",
        )
        db.add(target)
        db.flush()
        db.add_all(
            (
                PlatformRole(user_id=target.id, role="superadmin"),
                PlatformRole(user_id=target.id, role="operator"),
            )
        )
        db.commit()
        target_id = target.id

    response = client.delete(f"/v1/admin/platform-admins/{target_id}", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Superadministrator access is protected"

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        roles = set(
            db.scalars(select(PlatformRole.role).where(PlatformRole.user_id == target_id)).all()
        )
        assert roles == {"operator", "superadmin"}
        db.query(PlatformRole).filter(PlatformRole.user_id == target_id).delete()
        target = db.get(User, target_id)
        assert target is not None
        db.delete(target)
        db.commit()


def test_suspended_role_holder_can_be_deprivileged_through_both_revoke_routes(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "+2348099990000")
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        target = User(
            name="Suspended role holder",
            primary_phone_e164="+2348555555577",
            status="suspended",
        )
        db.add(target)
        db.flush()
        db.add_all(
            (
                PlatformRole(user_id=target.id, role="operator"),
                PlatformRole(user_id=target.id, role="researcher"),
            )
        )
        db.commit()
        target_id = target.id

    try:
        research_revoke = client.delete(
            f"/v1/admin/platform-access/{target_id}/researcher",
            headers=headers,
        )
        assert research_revoke.status_code == 204, research_revoke.text

        operator_revoke = client.delete(
            f"/v1/admin/platform-admins/{target_id}",
            headers=headers,
        )
        assert operator_revoke.status_code == 204, operator_revoke.text

        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            assert not db.scalars(
                select(PlatformRole).where(PlatformRole.user_id == target_id)
            ).all()
    finally:
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            db.query(PlatformRole).filter(PlatformRole.user_id == target_id).delete()
            target = db.get(User, target_id)
            if target is not None:
                db.delete(target)
            db.commit()


def test_temporary_pin_mode_grants_only_previously_mapped_collaborators(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "+2348099990000")
    mapped_phone = "+2348555555587"
    unmapped_phone = "+2348555555588"
    unknown_phone = "+2348555555589"
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        actor = db.scalar(select(User).where(User.primary_phone_e164 == "+2348099990000"))
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "demo-store"))
        assert actor is not None
        assert tenant is not None
        actor_membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=actor.id,
            role="admin",
            status="active",
        )
        actor_mapping = PhoneIdentity(
            tenant_id=tenant.id,
            user_id=actor.id,
            phone_e164=actor.primary_phone_e164,
            status="approved",
            opt_out=False,
        )
        db.add_all((actor_membership, actor_mapping))
        mapped = User(
            name="Mapped grant target",
            primary_phone_e164=mapped_phone,
            status="active",
        )
        unmapped = User(
            name="Unmapped role holder",
            primary_phone_e164=unmapped_phone,
            status="active",
        )
        db.add_all((mapped, unmapped))
        db.flush()
        mapped_membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=mapped.id,
            role="member",
            status="active",
        )
        mapped_mapping = PhoneIdentity(
            tenant_id=tenant.id,
            user_id=mapped.id,
            phone_e164=mapped.primary_phone_e164,
            status="approved",
            opt_out=False,
        )
        db.add_all(
            (
                mapped_membership,
                mapped_mapping,
                PlatformRole(user_id=unmapped.id, role="researcher"),
            )
        )
        db.commit()
        mapped_id = mapped.id
        unmapped_id = unmapped.id
        actor_membership_id = actor_membership.id
        actor_mapping_id = actor_mapping.id
        mapped_membership_id = mapped_membership.id
        mapped_mapping_id = mapped_mapping.id

    temporary_settings = get_settings().model_copy(
        update={"auth_login_mode": "temporary_static_pin"}
    )
    app.dependency_overrides[get_settings] = lambda: temporary_settings
    try:
        directory = client.get("/v1/admin/platform-access", headers=headers)
        assert directory.status_code == 200, directory.text
        # Existing role holders remain visible so their dormant privilege can
        # be audited and revoked, but they cannot receive another grant.
        unmapped_row = next(row for row in directory.json() if row["user_id"] == unmapped_id)
        assert unmapped_row["has_active_mapping"] is False

        existing_grant = client.put(
            f"/v1/admin/platform-access/{unmapped_id}/researcher",
            headers=headers,
        )
        assert existing_grant.status_code == 200, existing_grant.text
        assert existing_grant.json()["platform_roles"] == ["researcher"]

        direct_grant = client.put(
            f"/v1/admin/platform-access/{unmapped_id}/operator",
            headers=headers,
        )
        assert direct_grant.status_code == 409
        assert direct_grant.json()["detail"] == (
            "Map this collaborator to an active workspace before granting platform access"
        )

        legacy_unmapped = client.post(
            "/v1/admin/platform-admins",
            headers=headers,
            json={"name": "Unmapped role holder", "phone_e164": unmapped_phone},
        )
        assert legacy_unmapped.status_code == 409

        legacy_unknown = client.post(
            "/v1/admin/platform-admins",
            headers=headers,
            json={"name": "Must not be created", "phone_e164": unknown_phone},
        )
        assert legacy_unknown.status_code == 409
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            assert db.scalar(select(User).where(User.primary_phone_e164 == unknown_phone)) is None

        mapped_grant = client.post(
            "/v1/admin/platform-admins",
            headers=headers,
            json={"name": "Existing mapped target", "phone_e164": mapped_phone},
        )
        assert mapped_grant.status_code == 201, mapped_grant.text
        assert "operator" in mapped_grant.json()["platform_roles"]
    finally:
        app.dependency_overrides.pop(get_settings, None)
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            db.query(PlatformRole).filter(
                PlatformRole.user_id.in_((mapped_id, unmapped_id))
            ).delete(synchronize_session=False)
            for mapping_id in (actor_mapping_id, mapped_mapping_id):
                mapping = db.get(PhoneIdentity, mapping_id)
                if mapping is not None:
                    db.delete(mapping)
            for membership_id in (actor_membership_id, mapped_membership_id):
                membership = db.get(TenantMembership, membership_id)
                if membership is not None:
                    db.delete(membership)
            for user_id in (mapped_id, unmapped_id):
                target = db.get(User, user_id)
                if target is not None:
                    db.delete(target)
            db.commit()
