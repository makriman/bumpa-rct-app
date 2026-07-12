from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import Principal, require_tenant, require_tenant_admin
from app.core.security import normalize_phone
from app.db.models import BumpaConnection, McpConnection, PhoneIdentity, TenantMembership, User
from app.db.session import get_db
from app.schemas import McpConnectionCreate, PhoneCreate, ProfileUpdate, UserCreate
from app.services.audit import audit

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
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(principal.user, key, str(value))
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
    user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
    if not user:
        user = User(
            name=payload.name,
            primary_phone_e164=phone,
            email=str(payload.email) if payload.email else None,
        )
        db.add(user)
        db.flush()
    if db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == principal.tenant.id,
            TenantMembership.user_id == user.id,
        )
    ):
        raise HTTPException(status_code=409, detail="User is already a team member")
    membership = TenantMembership(tenant_id=principal.tenant.id, user_id=user.id, role=payload.role)
    db.add(membership)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="team.member.added",
        resource_type="membership",
        resource_id=membership.id,
        after={"user_id": user.id, "role": payload.role},
    )
    db.commit()
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
    membership.status = "revoked"
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="team.member.revoked",
        resource_type="membership",
        resource_id=membership.id,
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
    identity = PhoneIdentity(
        tenant_id=principal.tenant.id,
        user_id=payload.user_id,
        phone_e164=normalize_phone(payload.phone_e164),
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
    db.commit()
    return {"id": identity.id, "status": identity.status}


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
    return {
        "status": connection.status,
        "scope_type": connection.scope_type,
        "scope_id_last4": connection.scope_id[-4:],
        "provider": connection.provider,
        "last_successful_sync_at": connection.last_successful_sync_at,
        "last_error": connection.last_error,
    }


@router.get("/mcp-connections")
def mcp_connections(
    principal: Principal = Depends(require_tenant), db: Session = Depends(get_db)
) -> list[dict]:
    assert principal.tenant is not None
    rows = db.scalars(
        select(McpConnection).where(McpConnection.tenant_id == principal.tenant.id)
    ).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "status": row.status,
            "scopes": row.scopes,
            "read_only": row.read_only,
            "admin_approved": row.admin_approved,
        }
        for row in rows
    ]


@router.post("/mcp-connections", status_code=201)
def create_mcp_connection(
    payload: McpConnectionCreate,
    principal: Principal = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    assert principal.tenant is not None
    connection = McpConnection(
        tenant_id=principal.tenant.id,
        created_by=principal.user.id,
        provider=payload.provider,
        status="admin_pending",
        scopes=payload.scopes,
        read_only=payload.read_only,
    )
    db.add(connection)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id,
        action="mcp.connection.requested",
        resource_type="mcp_connection",
        resource_id=connection.id,
        after={"provider": payload.provider, "read_only": payload.read_only},
    )
    db.commit()
    return {"id": connection.id, "status": connection.status}
