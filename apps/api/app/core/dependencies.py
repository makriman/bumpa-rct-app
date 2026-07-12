from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.db.models import PlatformRole, Tenant, TenantMembership, User
from app.db.session import get_db, set_security_context

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


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
    settings: Settings = Depends(get_settings),
) -> str:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    cookie_name = settings.session_cookie_name
    token = request.cookies.get(cookie_name) or session_cookie
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    enforce_cookie_origin(request, settings)
    return token


def enforce_cookie_origin(request: Request, settings: Settings) -> None:
    """Protect unsafe cookie-authenticated requests from cross-site submission.

    Bearer-authenticated clients never enter this function. Local/test mode keeps
    direct test and developer clients frictionless; production browser origins
    must be same-origin or explicitly allowed by CORS configuration.
    """

    if request.method.upper() in SAFE_METHODS or settings.is_local:
        return
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        if _private_peer(request):
            return
        raise HTTPException(status_code=403, detail="Request origin could not be verified")
    source_origin = _normalized_origin(source)
    allowed = {
        origin
        for configured in settings.effective_cors_origins
        if (origin := _normalized_origin(configured)) is not None
    }
    request_origin = _normalized_origin(str(request.base_url))
    if request_origin:
        allowed.add(request_origin)
    if source_origin is None or source_origin not in allowed:
        raise HTTPException(status_code=403, detail="Request origin is not allowed")


def _normalized_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port
    except ValueError:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}{suffix}"


def _private_peer(request: Request) -> bool:
    if not request.client:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


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
