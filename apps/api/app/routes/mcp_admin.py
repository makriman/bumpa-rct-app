from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_operator
from app.db.models import McpConnection, McpToolPermission, Tenant
from app.db.session import get_db
from app.providers.redaction import redact_text
from app.schemas import (
    McpAdminConnectionView,
    McpAdminDecision,
    McpProvider,
    McpToolPermissionValue,
)
from app.services.audit import audit
from app.services.mcp_oauth import default_permissions, oauth_client, revoke_oauth_token

router = APIRouter(prefix="/admin/mcp-connections", tags=["admin", "mcp"])


@router.get("", response_model=list[McpAdminConnectionView])
def list_mcp_approval_requests(
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[McpAdminConnectionView]:
    statement = select(McpConnection, Tenant).join(Tenant, Tenant.id == McpConnection.tenant_id)
    if status is not None:
        statement = statement.where(McpConnection.status == status)
    rows = db.execute(
        statement.order_by(McpConnection.created_at.desc(), McpConnection.id.asc()).limit(limit)
    ).all()
    connection_ids = [connection.id for connection, _tenant in rows]
    permissions = (
        db.scalars(
            select(McpToolPermission).where(McpToolPermission.mcp_connection_id.in_(connection_ids))
        ).all()
        if connection_ids
        else []
    )
    by_connection: dict[str, dict[str, McpToolPermissionValue]] = {}
    for permission in permissions:
        by_connection.setdefault(permission.mcp_connection_id, {})[permission.tool_name] = cast(
            McpToolPermissionValue, permission.permission
        )
    return [
        _admin_view(connection, tenant, settings, by_connection.get(connection.id, {}))
        for connection, tenant in rows
    ]


@router.patch("/{connection_id}", response_model=McpAdminConnectionView)
def decide_mcp_approval(
    connection_id: str,
    payload: McpAdminDecision,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> McpAdminConnectionView:
    row = db.execute(
        select(McpConnection, Tenant)
        .join(Tenant, Tenant.id == McpConnection.tenant_id)
        .where(McpConnection.id == connection_id)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="MCP connection request not found")
    connection, tenant = row
    before = {
        "status": connection.status,
        "admin_approved": connection.admin_approved,
        "read_only": connection.read_only,
    }
    upstream_revocation_confirmed: bool | None = None
    if payload.decision == "approve":
        if connection.status not in {"admin_pending", "rejected"}:
            raise HTTPException(status_code=409, detail="Connection is not awaiting approval")
        connection.admin_approved = True
        connection.status = "approved"
        _seed_default_permissions(db, connection, principal.user.id)
        action = "mcp.connection.approved"
    else:
        if connection.status not in {"admin_pending", "approved", "oauth_in_progress", "active"}:
            raise HTTPException(
                status_code=409, detail="Connection cannot be rejected in this state"
            )
        upstream_revocation_confirmed = (
            True
            if settings.is_local
            else revoke_oauth_token(
                settings=settings,
                provider=cast(McpProvider, connection.provider),
                encrypted_credentials=connection.encrypted_credentials,
            )
        )
        connection.admin_approved = False
        connection.status = "rejected"
        connection.encrypted_credentials = None
        for permission in db.scalars(
            select(McpToolPermission).where(McpToolPermission.mcp_connection_id == connection.id)
        ).all():
            permission.permission = "deny"
            permission.created_by = principal.user.id
        action = "mcp.connection.rejected"
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=connection.tenant_id,
        action=action,
        resource_type="mcp_connection",
        resource_id=connection.id,
        before=before,
        after={
            "status": connection.status,
            "admin_approved": connection.admin_approved,
            "provider": connection.provider,
            "read_only": connection.read_only,
            "reason": redact_text(payload.reason),
            "upstream_revocation_confirmed": upstream_revocation_confirmed,
        },
    )
    db.commit()
    permissions = {
        row.tool_name: cast(McpToolPermissionValue, row.permission)
        for row in db.scalars(
            select(McpToolPermission).where(McpToolPermission.mcp_connection_id == connection.id)
        ).all()
    }
    return _admin_view(connection, tenant, settings, permissions)


def _seed_default_permissions(db: Session, connection: McpConnection, actor_user_id: str) -> None:
    provider = cast(McpProvider, connection.provider)
    expected = default_permissions(provider, read_only=connection.read_only)
    existing = {
        row.tool_name: row
        for row in db.scalars(
            select(McpToolPermission).where(McpToolPermission.mcp_connection_id == connection.id)
        ).all()
    }
    for tool_name, permission_value in expected.items():
        permission = existing.get(tool_name)
        if permission is None:
            db.add(
                McpToolPermission(
                    tenant_id=connection.tenant_id,
                    mcp_connection_id=connection.id,
                    tool_name=tool_name,
                    permission=permission_value,
                    created_by=actor_user_id,
                )
            )
        else:
            permission.permission = permission_value
            permission.created_by = actor_user_id


def _admin_view(
    connection: McpConnection,
    tenant: Tenant,
    settings: Settings,
    permissions: dict[str, McpToolPermissionValue],
) -> McpAdminConnectionView:
    provider = cast(McpProvider, connection.provider)
    return McpAdminConnectionView(
        id=connection.id,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        created_by=connection.created_by,
        provider=provider,
        status=connection.status,
        scopes=list(connection.scopes),
        read_only=connection.read_only,
        admin_approved=connection.admin_approved,
        oauth_available=oauth_client(settings, provider) is not None,
        permissions=permissions,
        created_at=connection.created_at,
    )
