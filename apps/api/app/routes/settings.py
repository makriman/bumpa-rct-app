import json
import time
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.dependencies import Principal, extract_token, require_tenant, require_tenant_admin
from app.core.security import decode_access_token, normalize_phone
from app.db.models import (
    BumpaConnection,
    BumpaSyncRun,
    McpConnection,
    McpToolPermission,
    PhoneIdentity,
    TenantMembership,
    User,
)
from app.db.session import get_db, set_security_context
from app.schemas import (
    McpConnectionCreate,
    McpConnectionView,
    McpOAuthStartView,
    McpProvider,
    McpToolPermissionUpdate,
    McpToolPermissionValue,
    PhoneCreate,
    ProfileUpdate,
    UserCreate,
)
from app.services.audit import audit
from app.services.bumpa_freshness import usable_bumpa_sync_run_predicate
from app.services.mcp_oauth import (
    McpOAuthError,
    build_authorization_url,
    connection_scopes,
    decode_oauth_state,
    exchange_authorization_code,
    oauth_client,
    revoke_oauth_token,
    validate_tool_permission,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/profile")
def profile(principal: Principal = Depends(require_tenant)) -> dict:
    return {
        "id": principal.user.id,
        "name": principal.user.name,
        "email": principal.user.email,
        "phone_e164": principal.user.primary_phone_e164,
    }


@router.patch("/profile")
def update_profile(
    payload: ProfileUpdate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        name = changes["name"]
        if name is None or not str(name).strip():
            raise HTTPException(status_code=422, detail="Name cannot be empty")
        changes["name"] = str(name).strip()
    for key, value in changes.items():
        setattr(principal.user, key, str(value) if value is not None else None)
    if changes:
        audit(
            db,
            actor_user_id=principal.user.id,
            tenant_id=principal.tenant.id if principal.tenant else None,
            action="user.profile.updated",
            resource_type="user",
            resource_id=principal.user.id,
            after={"changed_fields": sorted(changes)},
        )
    db.commit()
    return profile(principal)


@router.get("/team")
def team(
    principal: Principal = Depends(require_tenant), db: Session = Depends(get_db)
) -> list[dict]:
    assert principal.tenant is not None
    rows = db.execute(
        select(TenantMembership, User)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == principal.tenant.id)
    ).all()
    return [
        {
            "membership_id": membership.id,
            "user_id": user.id,
            "name": user.name,
            "email": user.email,
            "phone_e164": user.primary_phone_e164,
            "role": membership.role,
            "status": membership.status,
        }
        for membership, user in rows
    ]


@router.post("/team", status_code=201)
def add_team_member(
    payload: UserCreate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    assert principal.tenant is not None
    phone = normalize_phone(payload.phone_e164)
    try:
        user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
        if not user:
            user = User(
                name=payload.name,
                primary_phone_e164=phone,
                email=str(payload.email) if payload.email else None,
            )
            db.add(user)
            # The unique phone constraint closes the concurrent-new-user race.
            db.flush()
        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == principal.tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        action = "team.member.added"
        before: dict[str, str] | None = None
        if membership:
            if membership.role == "owner":
                raise HTTPException(
                    status_code=409, detail="The tenant owner is managed by the platform"
                )
            if membership.status == "active":
                raise HTTPException(status_code=409, detail="User is already an active team member")
            if membership.status != "revoked":
                raise HTTPException(status_code=409, detail="Team membership cannot be reactivated")
            before = {"role": membership.role, "status": membership.status}
            membership.role = payload.role
            membership.status = "active"
            action = "team.member.reactivated"
        else:
            membership = TenantMembership(
                tenant_id=principal.tenant.id,
                user_id=user.id,
                role=payload.role,
            )
            db.add(membership)
            # Populate the identifier and surface the membership uniqueness
            # race before audit creation.
            db.flush()
        audit(
            db,
            actor_user_id=principal.user.id,
            tenant_id=principal.tenant.id,
            action=action,
            resource_type="membership",
            resource_id=membership.id,
            before=before,
            after={"user_id": user.id, "role": payload.role, "status": "active"},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Team member conflict") from exc
    return {"membership_id": membership.id, "user_id": user.id, "role": membership.role}


@router.delete("/team/{membership_id}", status_code=204)
def remove_team_member(
    membership_id: str,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> None:
    membership = db.get(TenantMembership, membership_id)
    if not membership or not principal.tenant or membership.tenant_id != principal.tenant.id:
        raise HTTPException(status_code=404, detail="Membership not found")
    if membership.role == "owner":
        raise HTTPException(status_code=409, detail="The tenant owner cannot be removed")
    if membership.status == "revoked":
        return
    if membership.status != "active":
        raise HTTPException(status_code=409, detail="Team membership cannot be revoked")
    membership.status = "revoked"
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="team.member.revoked",
        resource_type="membership",
        resource_id=membership.id,
        before={"role": membership.role, "status": "active"},
        after={"role": membership.role, "status": "revoked"},
    )
    db.commit()


@router.get("/whatsapp-numbers")
def whatsapp_numbers(
    principal: Principal = Depends(require_tenant), db: Session = Depends(get_db)
) -> list[dict]:
    assert principal.tenant is not None
    rows = db.scalars(
        select(PhoneIdentity).where(PhoneIdentity.tenant_id == principal.tenant.id)
    ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "phone_e164": row.phone_e164,
            "label": row.label,
            "status": row.status,
            "opt_out": row.opt_out,
        }
        for row in rows
    ]


@router.post("/whatsapp-numbers", status_code=201)
def add_whatsapp_number(
    payload: PhoneCreate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    assert principal.tenant is not None
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == principal.tenant.id,
            TenantMembership.user_id == payload.user_id,
            TenantMembership.status == "active",
        )
    )
    if not membership:
        raise HTTPException(status_code=422, detail="User must be an active team member")
    phone = normalize_phone(payload.phone_e164)
    existing = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
    if existing:
        raise HTTPException(status_code=409, detail="WhatsApp number is already approved")
    identity = PhoneIdentity(
        tenant_id=principal.tenant.id,
        user_id=payload.user_id,
        phone_e164=phone,
        label=payload.label,
    )
    db.add(identity)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="phone.approved",
        resource_type="phone_identity",
        resource_id=identity.id,
        after={"user_id": payload.user_id},
    )
    try:
        db.commit()
    except IntegrityError:
        # The unique phone constraint remains the cross-tenant concurrency
        # authority. Convert races (and identities hidden by tenant RLS) into a
        # stable API conflict without ever reassigning the existing identity.
        db.rollback()
        raise HTTPException(status_code=409, detail="WhatsApp number is already approved") from None
    return {"id": identity.id, "status": identity.status}


@router.delete("/whatsapp-numbers/{identity_id}", status_code=204)
def remove_whatsapp_number(
    identity_id: str,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> None:
    assert principal.tenant is not None
    identity = db.scalar(
        select(PhoneIdentity).where(
            PhoneIdentity.id == identity_id,
            PhoneIdentity.tenant_id == principal.tenant.id,
        )
    )
    if identity is None:
        raise HTTPException(status_code=404, detail="WhatsApp number not found")
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == principal.tenant.id,
            TenantMembership.user_id == identity.user_id,
        )
    )
    if membership is not None and membership.role == "owner":
        raise HTTPException(
            status_code=409,
            detail="The owner WhatsApp mapping is managed by the platform",
        )
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="phone.revoked",
        resource_type="phone_identity",
        resource_id=identity.id,
        before={
            "user_id": identity.user_id,
            "label": identity.label,
            "status": identity.status,
        },
    )
    db.delete(identity)
    db.commit()


@router.get("/bumpa")
def bumpa_status(
    principal: Principal = Depends(require_tenant), db: Session = Depends(get_db)
) -> dict:
    assert principal.tenant is not None
    connection = db.scalar(
        select(BumpaConnection).where(BumpaConnection.tenant_id == principal.tenant.id)
    )
    if not connection:
        return {"status": "not_connected"}
    last_successful_sync_at = db.scalar(
        select(BumpaSyncRun.finished_at)
        .where(
            BumpaSyncRun.tenant_id == principal.tenant.id,
            usable_bumpa_sync_run_predicate(),
        )
        .order_by(BumpaSyncRun.finished_at.desc(), BumpaSyncRun.started_at.desc())
        .limit(1)
    )
    return {
        "status": connection.status,
        "scope_type": connection.scope_type,
        "scope_id_last4": connection.scope_id[-4:],
        "provider": connection.provider,
        "last_successful_sync_at": last_successful_sync_at,
        "last_error": connection.last_error,
    }


@router.get("/mcp-connections", response_model=list[McpConnectionView])
def mcp_connections(
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[McpConnectionView]:
    assert principal.tenant is not None
    rows = db.scalars(
        select(McpConnection)
        .where(McpConnection.tenant_id == principal.tenant.id)
        .order_by(McpConnection.created_at.asc(), McpConnection.id.asc())
    ).all()
    permissions = db.scalars(
        select(McpToolPermission).where(McpToolPermission.tenant_id == principal.tenant.id)
    ).all()
    by_connection: dict[str, dict[str, McpToolPermissionValue]] = {}
    for permission in permissions:
        by_connection.setdefault(permission.mcp_connection_id, {})[permission.tool_name] = cast(
            McpToolPermissionValue, permission.permission
        )
    return [_mcp_connection_view(row, settings, by_connection.get(row.id, {})) for row in rows]


@router.post("/mcp-connections", response_model=McpConnectionView, status_code=201)
def create_mcp_connection(
    payload: McpConnectionCreate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> McpConnectionView:
    assert principal.tenant is not None
    expected_scopes = connection_scopes(payload.provider, read_only=payload.read_only)
    if payload.scopes and set(payload.scopes) != set(expected_scopes):
        raise HTTPException(
            status_code=422,
            detail="Connector scopes must match the approved provider registry",
        )
    existing = db.scalar(
        select(McpConnection).where(
            McpConnection.tenant_id == principal.tenant.id,
            McpConnection.provider == payload.provider,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="This connector has already been requested")
    connection = McpConnection(
        tenant_id=principal.tenant.id,
        created_by=principal.user.id,
        provider=payload.provider,
        status="admin_pending",
        scopes=expected_scopes,
        read_only=payload.read_only,
    )
    db.add(connection)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This connector has already been requested"
        ) from exc
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="mcp.connection.requested",
        resource_type="mcp_connection",
        resource_id=connection.id,
        after={
            "provider": payload.provider,
            "read_only": payload.read_only,
            "scopes": expected_scopes,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This connector has already been requested"
        ) from exc
    return _mcp_connection_view(connection, settings, {})


@router.delete("/mcp-connections/{connection_id}", status_code=204)
def delete_mcp_connection(
    connection_id: str,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    connection = _tenant_mcp_connection(db, principal, connection_id)
    upstream_revocation_confirmed = (
        True
        if settings.is_local
        else revoke_oauth_token(
            settings=settings,
            provider=cast(McpProvider, connection.provider),
            encrypted_credentials=connection.encrypted_credentials,
        )
    )
    before = {
        "provider": connection.provider,
        "status": connection.status,
        "read_only": connection.read_only,
        "admin_approved": connection.admin_approved,
    }
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=connection.tenant_id,
        action="mcp.connection.revoked",
        resource_type="mcp_connection",
        resource_id=connection.id,
        before=before,
        after={"upstream_revocation_confirmed": upstream_revocation_confirmed},
    )
    connection.encrypted_credentials = None
    db.flush()
    db.delete(connection)
    db.commit()


@router.patch(
    "/mcp-connections/{connection_id}/permissions/{tool_name}",
    response_model=McpConnectionView,
)
def update_mcp_permission(
    connection_id: str,
    tool_name: str,
    payload: McpToolPermissionUpdate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> McpConnectionView:
    connection = _tenant_mcp_connection(db, principal, connection_id)
    if not connection.admin_approved:
        raise HTTPException(status_code=409, detail="Operator approval is required first")
    provider = cast(McpProvider, connection.provider)
    try:
        validate_tool_permission(
            provider,
            tool_name,
            payload.permission,
            read_only=connection.read_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    permission = db.scalar(
        select(McpToolPermission).where(
            McpToolPermission.tenant_id == connection.tenant_id,
            McpToolPermission.mcp_connection_id == connection.id,
            McpToolPermission.tool_name == tool_name,
        )
    )
    before = {"permission": permission.permission} if permission else None
    if permission is None:
        permission = McpToolPermission(
            tenant_id=connection.tenant_id,
            mcp_connection_id=connection.id,
            tool_name=tool_name,
            permission=payload.permission,
            created_by=principal.user.id,
        )
        db.add(permission)
    else:
        permission.permission = payload.permission
        permission.created_by = principal.user.id
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=connection.tenant_id,
        action="mcp.tool_permission.updated",
        resource_type="mcp_tool_permission",
        resource_id=permission.id,
        before=before,
        after={
            "connection_id": connection.id,
            "tool_name": tool_name,
            "permission": payload.permission,
            "write_confirmation_required": payload.permission == "write_with_confirmation",
        },
    )
    db.commit()
    return _mcp_connection_view(
        connection,
        settings,
        _connection_permissions(db, connection.id),
    )


@router.post(
    "/mcp-connections/{connection_id}/oauth/start",
    response_model=McpOAuthStartView,
)
def start_mcp_oauth(
    connection_id: str,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> McpOAuthStartView:
    connection = _tenant_mcp_connection(db, principal, connection_id)
    if not connection.admin_approved or connection.status not in {
        "approved",
        "active",
        "oauth_in_progress",
    }:
        raise HTTPException(status_code=409, detail="Operator approval is required first")
    try:
        authorization_url, expires_at = build_authorization_url(
            settings=settings,
            connection_id=connection.id,
            tenant_id=connection.tenant_id,
            user_id=principal.user.id,
            provider=cast(McpProvider, connection.provider),
            read_only=connection.read_only,
        )
    except McpOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    before_status = connection.status
    connection.status = "oauth_in_progress"
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=connection.tenant_id,
        action="mcp.oauth.started",
        resource_type="mcp_connection",
        resource_id=connection.id,
        before={"status": before_status},
        after={"status": connection.status, "provider": connection.provider},
    )
    db.commit()
    remaining = max(0, expires_at - int(time.time()))
    return McpOAuthStartView(
        authorization_url=authorization_url,
        expires_in_seconds=remaining,
    )


@router.get("/mcp-oauth/callback", response_class=RedirectResponse)
def complete_mcp_oauth(
    state: str = Query(min_length=20, max_length=8192),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=160),
    token: str = Depends(extract_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        oauth_state = decode_oauth_state(state, settings)
    except McpOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_security_context(db, privileged=True)
    user = decode_access_token(db, token, settings)
    if user.id != oauth_state.user_id:
        raise HTTPException(status_code=403, detail="OAuth state does not belong to this user")
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == oauth_state.tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
            TenantMembership.role.in_(("owner", "admin")),
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Tenant administrator access is required")
    connection = db.scalar(
        select(McpConnection).where(
            McpConnection.id == oauth_state.connection_id,
            McpConnection.tenant_id == oauth_state.tenant_id,
        )
    )
    if (
        connection is None
        or connection.provider != oauth_state.provider
        or not connection.admin_approved
        or connection.status != "oauth_in_progress"
    ):
        raise HTTPException(status_code=409, detail="OAuth connection is no longer pending")
    fallback_status = "active" if connection.encrypted_credentials else "approved"
    if error is not None:
        connection.status = fallback_status
        audit(
            db,
            actor_user_id=user.id,
            tenant_id=connection.tenant_id,
            action="mcp.oauth.cancelled",
            resource_type="mcp_connection",
            resource_id=connection.id,
            after={"provider": connection.provider, "status": connection.status},
        )
        db.commit()
        return _mcp_redirect(settings, "cancelled")
    if code is None:
        raise HTTPException(status_code=400, detail="OAuth provider did not return a code")
    try:
        token_bundle = exchange_authorization_code(
            settings=settings,
            provider=oauth_state.provider,
            code=code,
            verifier=oauth_state.verifier,
        )
    except McpOAuthError:
        connection.status = fallback_status
        audit(
            db,
            actor_user_id=user.id,
            tenant_id=connection.tenant_id,
            action="mcp.oauth.failed",
            resource_type="mcp_connection",
            resource_id=connection.id,
            after={"provider": connection.provider, "status": connection.status},
        )
        db.commit()
        return _mcp_redirect(settings, "error")
    connection.encrypted_credentials = FieldCipher.from_settings(settings).encrypt(
        json.dumps(token_bundle, separators=(",", ":"), sort_keys=True)
    )
    connection.scopes = connection_scopes(
        oauth_state.provider,
        read_only=connection.read_only,
    )
    connection.status = "active"
    audit(
        db,
        actor_user_id=user.id,
        tenant_id=connection.tenant_id,
        action="mcp.oauth.completed",
        resource_type="mcp_connection",
        resource_id=connection.id,
        after={
            "provider": connection.provider,
            "status": connection.status,
            "scopes": connection.scopes,
        },
    )
    db.commit()
    return _mcp_redirect(settings, "success")


def _tenant_mcp_connection(
    db: Session,
    principal: Principal,
    connection_id: str,
) -> McpConnection:
    assert principal.tenant is not None
    connection = db.scalar(
        select(McpConnection).where(
            McpConnection.id == connection_id,
            McpConnection.tenant_id == principal.tenant.id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="MCP connection not found")
    return connection


def _connection_permissions(db: Session, connection_id: str) -> dict[str, McpToolPermissionValue]:
    rows = db.scalars(
        select(McpToolPermission).where(McpToolPermission.mcp_connection_id == connection_id)
    ).all()
    return {row.tool_name: cast(McpToolPermissionValue, row.permission) for row in rows}


def _mcp_connection_view(
    connection: McpConnection,
    settings: Settings,
    permissions: dict[str, McpToolPermissionValue],
) -> McpConnectionView:
    provider = cast(McpProvider, connection.provider)
    return McpConnectionView(
        id=connection.id,
        provider=provider,
        status=connection.status,
        scopes=list(connection.scopes),
        read_only=connection.read_only,
        admin_approved=connection.admin_approved,
        oauth_available=oauth_client(settings, provider) is not None,
        permissions=permissions,
    )


def _mcp_redirect(settings: Settings, outcome: str) -> RedirectResponse:
    return RedirectResponse(
        f"{settings.public_origin.rstrip('/')}/settings/mcp?oauth={outcome}",
        status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
