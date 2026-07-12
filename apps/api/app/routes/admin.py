from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.core.dependencies import Principal, require_operator, require_superadmin
from app.core.security import normalize_phone
from app.db.models import (
    AsyncJob,
    AuditLog,
    BumpaConnection,
    BumpaSyncRun,
    HermesProfile,
    PhoneIdentity,
    PlatformRole,
    SystemError,
    Tenant,
    TenantMembership,
    UsageEvent,
    User,
)
from app.db.session import get_db
from app.jobs.runtime import replay_dead_letter
from app.providers.bumpa import BumpaClient, BumpaProviderError
from app.providers.hermes import (
    HermesError,
    HermesProfileError,
    provision_profile,
    refresh_profile_status,
)
from app.providers.local import local_profile_key
from app.schemas import (
    AsyncJobReplayRequest,
    AsyncJobStatus,
    AsyncJobView,
    BumpaConnectionCreate,
    PhoneCreate,
    PlatformAdminCreate,
    PlatformAdminRole,
    PlatformAdminView,
    TenantCreate,
    TenantUpdate,
    TenantUserCreate,
)
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/platform-admins", response_model=list[PlatformAdminView])
def list_platform_admins(
    _principal: Principal = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> list[PlatformAdminView]:
    """Return platform administrators without sessions or other credentials."""

    users = db.scalars(
        select(User)
        .join(PlatformRole, PlatformRole.user_id == User.id)
        .where(PlatformRole.role.in_(("operator", "superadmin")))
        .distinct()
        .order_by(User.created_at.asc(), User.id.asc())
    ).all()
    roles_by_user = _admin_roles(db, [user.id for user in users])
    return [_platform_admin_view(user, roles_by_user.get(user.id, [])) for user in users]


@router.post("/platform-admins", response_model=PlatformAdminView, status_code=201)
def grant_platform_admin(
    payload: PlatformAdminCreate,
    principal: Principal = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> PlatformAdminView:
    """Grant operator access; only a superadministrator may call this endpoint."""

    phone = normalize_phone(payload.phone_e164)
    try:
        user = db.scalar(select(User).where(User.primary_phone_e164 == phone))
        created_user = False
        if user is None:
            user = User(name=payload.name, primary_phone_e164=phone, status="active")
            db.add(user)
            # The unique phone constraint is the final authority when two
            # superadmins grant the same new identity concurrently.
            db.flush()
            created_user = True
        elif user.status != "active":
            raise HTTPException(status_code=409, detail="User is not active")

        existing = db.scalar(
            select(PlatformRole).where(
                PlatformRole.user_id == user.id,
                PlatformRole.role == "operator",
            )
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="User is already a platform administrator")

        role = PlatformRole(user_id=user.id, role="operator")
        db.add(role)
        # Keep the role uniqueness race inside the same stable conflict
        # boundary as user creation.
        db.flush()
        audit(
            db,
            actor_user_id=principal.user.id,
            action="platform.operator.granted",
            resource_type="platform_role",
            resource_id=role.id,
            after={"user_id": user.id, "role": "operator", "user_created": created_user},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Platform administrator conflict") from exc
    roles = _admin_roles(db, [user.id]).get(user.id, [])
    return _platform_admin_view(user, roles)


@router.delete("/platform-admins/{user_id}", status_code=204)
def revoke_platform_admin(
    user_id: str,
    principal: Principal = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> None:
    """Revoke only the operator grant while preserving any superadmin role."""

    if user_id == principal.user.id:
        raise HTTPException(status_code=409, detail="You cannot revoke your own operator access")
    role = db.scalar(
        select(PlatformRole)
        .where(PlatformRole.user_id == user_id, PlatformRole.role == "operator")
        .with_for_update()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Platform administrator not found")

    target_has_superadmin = (
        db.scalar(
            select(PlatformRole.id).where(
                PlatformRole.user_id == user_id,
                PlatformRole.role == "superadmin",
            )
        )
        is not None
    )
    active_admin_ids = set(
        db.scalars(
            select(PlatformRole.user_id)
            .join(User, User.id == PlatformRole.user_id)
            .where(
                PlatformRole.role.in_(("operator", "superadmin")),
                User.status == "active",
            )
        ).all()
    )
    if not target_has_superadmin and active_admin_ids == {user_id}:
        raise HTTPException(
            status_code=409, detail="The last active platform administrator cannot be revoked"
        )

    role_id = role.id
    db.delete(role)
    audit(
        db,
        actor_user_id=principal.user.id,
        action="platform.operator.revoked",
        resource_type="platform_role",
        resource_id=role_id,
        before={"user_id": user_id, "role": "operator"},
    )
    db.commit()


@router.get("/tenants")
def list_tenants(
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict]:
    rows = db.scalars(select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)).all()
    return [_tenant_view(row) for row in rows]


@router.post("/tenants", status_code=201)
def create_tenant(
    payload: TenantCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = Tenant(**payload.model_dump())
    db.add(tenant)
    audit(
        db,
        actor_user_id=principal.user.id,
        action="tenant.created",
        resource_type="tenant",
        resource_id=tenant.id,
        after={"slug": tenant.slug, "name": tenant.name},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tenant slug already exists") from exc
    return _tenant_view(tenant)


@router.get("/tenants/{tenant_id}")
def get_tenant(
    tenant_id: str,
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    return _tenant_view(tenant)


@router.patch("/tenants/{tenant_id}")
def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    before = {"name": tenant.name, "status": tenant.status}
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(tenant, key, value)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.updated",
        resource_type="tenant",
        resource_id=tenant.id,
        before=before,
        after=payload.model_dump(exclude_none=True),
    )
    db.commit()
    return _tenant_view(tenant)


@router.post("/tenants/{tenant_id}/users", status_code=201)
def create_user(
    tenant_id: str,
    payload: TenantUserCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
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
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id
        )
    )
    if membership:
        raise HTTPException(status_code=409, detail="User already belongs to this tenant")
    membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role=payload.role)
    db.add(membership)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.user.added",
        resource_type="membership",
        resource_id=membership.id,
        after={"user_id": user.id, "role": payload.role},
    )
    db.commit()
    return {"user_id": user.id, "membership_id": membership.id, "role": membership.role}


@router.post("/tenants/{tenant_id}/phones", status_code=201)
def create_phone(
    tenant_id: str,
    payload: PhoneCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _tenant(db, tenant_id)
    member = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == payload.user_id
        )
    )
    if not member:
        raise HTTPException(status_code=422, detail="User is not a tenant member")
    identity = PhoneIdentity(
        tenant_id=tenant.id,
        user_id=payload.user_id,
        phone_e164=normalize_phone(payload.phone_e164),
        label=payload.label,
    )
    db.add(identity)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="phone.approved",
        resource_type="phone_identity",
        resource_id=identity.id,
    )
    db.commit()
    return {"id": identity.id, "status": identity.status}


@router.post("/tenants/{tenant_id}/bumpa")
def connect_bumpa(
    tenant_id: str,
    payload: BumpaConnectionCreate,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    tenant = _tenant(db, tenant_id)
    if not settings.is_local and payload.provider == "local":
        raise HTTPException(
            status_code=422,
            detail="Local Bumpa provider is forbidden in production",
        )
    if payload.provider == "bumpa" and settings.bumpa_backend == "bumpa":
        try:
            with BumpaClient(payload.api_key, payload.scope_type, payload.scope_id) as provider:
                provider.verify()
        except (BumpaProviderError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="Bumpa connection verification failed"
            ) from exc
    connection = db.scalar(select(BumpaConnection).where(BumpaConnection.tenant_id == tenant.id))
    encrypted = FieldCipher(settings.field_encryption_key).encrypt(payload.api_key)
    if connection:
        connection.encrypted_api_key = encrypted
        connection.scope_type = payload.scope_type
        connection.scope_id = payload.scope_id
        connection.provider = payload.provider
        connection.status = "active"
    else:
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=encrypted,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            provider=payload.provider,
        )
        db.add(connection)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="tenant.bumpa_connection.saved",
        resource_type="bumpa_connection",
        resource_id=connection.id,
        after={
            "scope_type": payload.scope_type,
            "scope_id_last4": payload.scope_id[-4:],
            "provider": payload.provider,
        },
    )
    db.commit()
    return {"id": connection.id, "status": connection.status, "provider": connection.provider}


@router.post("/tenants/{tenant_id}/hermes-profile")
def create_profile(
    tenant_id: str,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    tenant = _tenant(db, tenant_id)
    if settings.agent_backend == "disabled":
        raise HTTPException(
            status_code=503,
            detail="Hermes profile provisioning is disabled",
        )
    existing = db.scalar(select(HermesProfile).where(HermesProfile.tenant_id == tenant.id))
    if existing:
        if settings.agent_backend == "hermes" and existing.provider == "hermes":
            try:
                refresh_profile_status(existing, settings)
            except HermesError:
                existing.status = "degraded"
            db.commit()
        return {"id": existing.id, "profile_name": existing.profile_name, "status": existing.status}
    if settings.agent_backend == "hermes":
        try:
            profile = provision_profile(db, tenant, settings)
        except HermesProfileError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit(
            db,
            actor_user_id=principal.user.id,
            tenant_id=tenant.id,
            action="hermes.profile.provisioned",
            resource_type="hermes_profile",
            resource_id=profile.id,
            after={
                "profile_name": profile.profile_name,
                "provider": "hermes",
                "api_port": profile.api_port,
                "status": profile.status,
            },
        )
        db.commit()
        return {
            "id": profile.id,
            "profile_name": profile.profile_name,
            "status": profile.status,
        }
    profile = HermesProfile(
        tenant_id=tenant.id,
        profile_name=f"tenant_{tenant.slug.replace('-', '_')}_{tenant.id[:8]}",
        provider="local",
        api_internal_url="local://agent",
        encrypted_api_key=FieldCipher(settings.field_encryption_key).encrypt(local_profile_key()),
    )
    db.add(profile)
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=tenant.id,
        action="hermes.profile.created",
        resource_type="hermes_profile",
        resource_id=profile.id,
        after={"profile_name": profile.profile_name, "provider": "local"},
    )
    db.commit()
    return {"id": profile.id, "profile_name": profile.profile_name, "status": profile.status}


@router.get("/system/errors")
def system_errors(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(select(SystemError).order_by(SystemError.created_at.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "service": row.service,
            "severity": row.severity,
            "message": row.message,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/system/jobs", response_model=list[AsyncJobView])
def async_jobs(
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    status: AsyncJobStatus | None = Query(default=None),
    tenant_id: str | None = Query(default=None, min_length=36, max_length=36),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AsyncJobView]:
    """List durable jobs through a deliberately payload-free operator projection."""

    statement = select(AsyncJob)
    if status is not None:
        statement = statement.where(AsyncJob.status == status)
    if tenant_id is not None:
        statement = statement.where(AsyncJob.tenant_id == tenant_id)
    rows = db.scalars(statement.order_by(AsyncJob.created_at.desc()).limit(limit)).all()
    return [_async_job_view(row) for row in rows]


@router.post("/system/jobs/{job_id}/replay", response_model=AsyncJobView)
def replay_async_job(
    job_id: str,
    payload: AsyncJobReplayRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AsyncJobView:
    """Replay one dead-letter job and write its audit record atomically."""

    job = db.scalar(select(AsyncJob).where(AsyncJob.id == job_id).with_for_update())
    if not job:
        raise HTTPException(status_code=404, detail="Asynchronous job not found")
    if job.status != "dead_letter":
        raise HTTPException(status_code=409, detail="Only dead-letter jobs can be replayed")
    before = {
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
    }
    try:
        replayed = replay_dead_letter(
            db,
            job.id,
            max_attempts=payload.max_attempts,
            commit=False,
        )
        audit(
            db,
            actor_user_id=principal.user.id,
            tenant_id=replayed.tenant_id,
            action="async_job.replayed",
            resource_type="async_job",
            resource_id=replayed.id,
            before=before,
            after={
                "status": replayed.status,
                "attempts": replayed.attempts,
                "max_attempts": replayed.max_attempts,
                "reason": payload.reason,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _async_job_view(replayed)


@router.get("/system/sync-runs")
def all_sync_runs(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(
        select(BumpaSyncRun).order_by(BumpaSyncRun.started_at.desc()).limit(100)
    ).all()
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "status": row.status,
            "started_at": row.started_at,
            "error": row.error,
        }
        for row in rows
    ]


@router.get("/usage")
def usage(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(select(UsageEvent).order_by(UsageEvent.created_at.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "event_name": row.event_name,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/audit")
def audits(
    _principal: Principal = Depends(require_operator), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def _tenant(db: Session, tenant_id: str) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def _admin_roles(db: Session, user_ids: list[str]) -> dict[str, list[PlatformAdminRole]]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(PlatformRole.user_id, PlatformRole.role).where(
            PlatformRole.user_id.in_(user_ids),
            PlatformRole.role.in_(("operator", "superadmin")),
        )
    ).all()
    result: dict[str, list[PlatformAdminRole]] = {}
    for user_id, role in rows:
        result.setdefault(user_id, []).append(cast(PlatformAdminRole, role))
    for roles in result.values():
        roles.sort(key=lambda value: (value != "superadmin", value))
    return result


def _platform_admin_view(user: User, roles: list[PlatformAdminRole]) -> PlatformAdminView:
    return PlatformAdminView(
        user_id=user.id,
        name=user.name,
        phone_e164=user.primary_phone_e164,
        status=user.status,
        platform_roles=roles,
        created_at=user.created_at,
    )


def _tenant_view(tenant: Tenant) -> dict:
    return {
        "id": tenant.id,
        "slug": tenant.slug,
        "name": tenant.name,
        "status": tenant.status,
        "business_category": tenant.business_category,
        "country": tenant.country,
        "city": tenant.city,
        "timezone": tenant.timezone,
        "currency_code": tenant.currency_code,
        "research_consent_status": tenant.research_consent_status,
        "created_at": tenant.created_at,
    }


def _async_job_view(job: AsyncJob) -> AsyncJobView:
    failure_category: str | None = None
    if job.last_error:
        if job.last_error.startswith("PermanentJobError:"):
            failure_category = "permanent_failure"
        elif job.last_error.startswith("Stale worker lease"):
            failure_category = "stale_lease"
        else:
            failure_category = "execution_failure"
    return AsyncJobView(
        id=job.id,
        tenant_id=job.tenant_id,
        kind=job.kind,
        status=cast(AsyncJobStatus, job.status),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        failure_category=failure_category,
        replayable=job.status == "dead_letter",
        available_at=job.available_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
