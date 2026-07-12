from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.db.models import PlatformRole, Tenant, TenantMembership, User
from app.db.session import get_db, set_security_context


@dataclass(frozen=True)
class Principal:
    user: User
    platform_roles: frozenset[str]
    membership: TenantMembership | None
    tenant: Tenant | None

    def has_platform_role(self, *roles: str) -> bool:
        return bool(self.platform_roles.intersection(roles))


def extract_token(
    request: Request,
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias="bb_session"),
) -> str:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    cookie_name = get_settings().session_cookie_name
    token = request.cookies.get(cookie_name) or session_cookie
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return token


def get_principal(
    token: str = Depends(extract_token),
    requested_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    # Authentication and membership resolution are privileged; normal users are narrowed to one
    # tenant immediately afterwards and Postgres reapplies that context after every commit.
    set_security_context(db, privileged=True)
    user = decode_access_token(db, token, settings)
    platform_roles = frozenset(
        db.scalars(select(PlatformRole.role).where(PlatformRole.user_id == user.id)).all()
    )
    memberships = list(
        db.scalars(
            select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.status == "active",
            )
        ).all()
    )
    membership: TenantMembership | None = None
    if requested_tenant_id:
        membership = next((m for m in memberships if m.tenant_id == requested_tenant_id), None)
        if not membership and not platform_roles.intersection(
            {"operator", "researcher", "superadmin"}
        ):
            raise HTTPException(status_code=403, detail="Tenant access denied")
    elif len(memberships) == 1:
        membership = memberships[0]
    elif memberships:
        membership = memberships[0]
    selected_tenant_id = requested_tenant_id or (membership.tenant_id if membership else None)
    tenant = db.get(Tenant, selected_tenant_id) if selected_tenant_id else None
    if tenant and tenant.status != "active" and "superadmin" not in platform_roles:
        raise HTTPException(status_code=403, detail="Tenant is not active")
    if not platform_roles.intersection({"operator", "researcher", "superadmin"}):
        set_security_context(db, tenant_id=tenant.id if tenant else None)
    return Principal(user, platform_roles, membership, tenant)


def require_tenant(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.tenant or not principal.membership:
        raise HTTPException(status_code=403, detail="An active tenant membership is required")
    return principal


def require_tenant_admin(principal: Principal = Depends(require_tenant)) -> Principal:
    if principal.membership and principal.membership.role in {"owner", "admin"}:
        return principal
    raise HTTPException(status_code=403, detail="Tenant administrator access required")


def require_operator(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.has_platform_role("operator", "superadmin"):
        return principal
    raise HTTPException(status_code=403, detail="Operator access required")


def require_researcher(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.has_platform_role("researcher", "superadmin"):
        return principal
    raise HTTPException(status_code=403, detail="Researcher access required")
