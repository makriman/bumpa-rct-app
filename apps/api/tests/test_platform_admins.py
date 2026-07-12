from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import dependencies
from app.db.models import AuditLog, PlatformRole, TenantMembership, User
from app.db.session import SessionLocal, set_security_context
from tests.conftest import auth_headers


def test_platform_admin_management_is_superadmin_only_audited_and_normalized(
    client: TestClient,
) -> None:
    operator_headers = auth_headers(client, "+2348099990001")
    assert client.get("/v1/admin/platform-admins", headers=operator_headers).status_code == 403
    assert (
        client.post(
            "/v1/admin/platform-admins",
            headers=operator_headers,
            json={"name": "Forbidden Grant", "phone_e164": "+2348555555500"},
        ).status_code
        == 403
    )

    superadmin_headers = auth_headers(client, "+2348099990000")
    created = client.post(
        "/v1/admin/platform-admins",
        headers=superadmin_headers,
        json={
            "name": "Secondary Platform Admin",
            "phone_e164": "+234 (855) 555-5501",
            "role": "operator",
        },
    )
    assert created.status_code == 201, created.text
    view = created.json()
    assert view == {
        "user_id": view["user_id"],
        "name": "Secondary Platform Admin",
        "phone_e164": "+2348555555501",
        "status": "active",
        "platform_roles": ["operator"],
        "created_at": view["created_at"],
    }
    assert "token" not in created.text.lower()

    duplicate = client.post(
        "/v1/admin/platform-admins",
        headers=superadmin_headers,
        json={"name": "Ignored Rename", "phone_e164": "+2348555555501"},
    )
    assert duplicate.status_code == 409

    listed = client.get("/v1/admin/platform-admins", headers=superadmin_headers)
    assert listed.status_code == 200
    listed_view = next(row for row in listed.json() if row["user_id"] == view["user_id"])
    assert listed_view["name"] == view["name"]
    assert listed_view["phone_e164"] == view["phone_e164"]
    assert listed_view["platform_roles"] == ["operator"]

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        grant_audit = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "platform.operator.granted",
                AuditLog.resource_id.is_not(None),
            )
            .order_by(AuditLog.created_at.desc())
        )
        assert grant_audit is not None
        assert grant_audit.after == {
            "user_id": view["user_id"],
            "role": "operator",
            "user_created": True,
        }
        assert "+2348555555501" not in str(grant_audit.after)

    revoked = client.delete(
        f"/v1/admin/platform-admins/{view['user_id']}",
        headers=superadmin_headers,
    )
    assert revoked.status_code == 204, revoked.text
    assert revoked.content == b""

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        assert (
            db.scalar(
                select(PlatformRole).where(
                    PlatformRole.user_id == view["user_id"],
                    PlatformRole.role == "operator",
                )
            )
            is None
        )
        revoke_audit = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "platform.operator.revoked")
            .order_by(AuditLog.created_at.desc())
        )
        assert revoke_audit is not None


def test_platform_admin_revocation_forbids_self_and_missing_grants(client: TestClient) -> None:
    superadmin_headers = auth_headers(client, "+2348099990000")
    admins = client.get("/v1/admin/platform-admins", headers=superadmin_headers).json()
    self_admin = next(row for row in admins if row["phone_e164"] == "+2348099990000")
    self_revoke = client.delete(
        f"/v1/admin/platform-admins/{self_admin['user_id']}",
        headers=superadmin_headers,
    )
    assert self_revoke.status_code == 409
    assert self_revoke.json()["detail"] == "You cannot revoke your own operator access"

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        owner = db.scalar(select(User).where(User.primary_phone_e164 == "+2348012345678"))
        assert owner is not None
        owner_id = owner.id
    assert (
        client.delete(
            f"/v1/admin/platform-admins/{owner_id}",
            headers=superadmin_headers,
        ).status_code
        == 404
    )


def test_concurrent_platform_admin_grant_returns_stable_conflict(
    client: TestClient, monkeypatch
) -> None:
    headers = auth_headers(client, "+2348099990000")
    original_flush = Session.flush
    conflict_raised = False

    def conflict_on_role(self: Session, *args, **kwargs) -> None:
        nonlocal conflict_raised
        if not conflict_raised and any(isinstance(row, PlatformRole) for row in self.new):
            conflict_raised = True
            raise IntegrityError("INSERT platform_roles", {}, RuntimeError("unique race"))
        original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", conflict_on_role)
    response = client.post(
        "/v1/admin/platform-admins",
        headers=headers,
        json={"name": "Concurrent Admin", "phone_e164": "+2348555555502"},
    )

    assert conflict_raised
    assert response.status_code == 409
    assert response.json() == {"detail": "Platform administrator conflict"}


def test_dual_role_admin_is_narrowed_on_tenant_routes_but_admin_routes_remain_global(
    client: TestClient, monkeypatch
) -> None:
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        owner = db.scalar(select(User).where(User.primary_phone_e164 == "+2348012345678"))
        assert owner is not None
        owner_id = owner.id
        role = PlatformRole(user_id=owner.id, role="operator")
        db.add(role)
        db.commit()
        role_id = role.id

    # Resolve the actual demo tenant for this owner rather than relying on list order.
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        owner_membership = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == owner_id)
        )
        assert owner_membership is not None
        tenant_id = owner_membership.tenant_id

    headers = auth_headers(client, "+2348012345678", tenant_id)
    contexts: list[tuple[bool, str | None]] = []

    def record_context(
        _db: Session,
        *,
        tenant_id: str | None = None,
        privileged: bool = False,
    ) -> None:
        contexts.append((privileged, tenant_id))

    monkeypatch.setattr(dependencies, "set_security_context", record_context)
    tenant_response = client.get("/v1/tenants/current", headers=headers)
    assert tenant_response.status_code == 200
    assert contexts == [(True, None), (False, tenant_id)]

    contexts.clear()
    admin_response = client.get("/v1/admin/tenants", headers=headers)
    assert admin_response.status_code == 200
    assert contexts == [(True, None)]

    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        persisted_role = db.get(PlatformRole, role_id)
        if persisted_role is not None:
            db.delete(persisted_role)
            db.commit()
