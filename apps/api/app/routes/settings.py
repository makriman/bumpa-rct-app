from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import Principal, require_tenant, require_tenant_admin
from app.core.security import normalize_phone
from app.db.models import (
    BumpaConnection,
    BumpaSyncRun,
    McpConnection,
    PhoneIdentity,
    TenantMembership,
    User,
)
from app.db.session import get_db
from app.schemas import McpConnectionCreate, PhoneCreate, ProfileUpdate, UserCreate
from app.services.audit import audit
from app.services.bumpa_freshness import usable_bumpa_sync_run_predicate

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
