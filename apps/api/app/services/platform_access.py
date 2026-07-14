from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import PhoneIdentity, PlatformRole, Tenant, TenantMembership, User
from app.services.audit import audit

PlatformAccessRole = Literal["operator", "researcher"]


class PlatformAccessError(Exception):
    """Base class for stable platform-access service failures."""


class PlatformAccessTargetNotFound(PlatformAccessError):
    pass


class PlatformAccessTargetInactive(PlatformAccessError):
    pass


class PlatformAccessTargetUnmapped(PlatformAccessError):
    pass


class ProtectedSuperadmin(PlatformAccessError):
    pass


class PlatformAccessConflict(PlatformAccessError):
    pass


@dataclass(frozen=True)
class PlatformAccessMutation:
    user: User
    changed: bool
    role_id: str | None


def grant_platform_access(
    db: Session,
    *,
    actor_user_id: str,
    user_id: str,
    role: PlatformAccessRole,
    user_created: bool = False,
    require_mapped_collaborator: bool = False,
) -> PlatformAccessMutation:
    """Idempotently grant one manageable platform role to an active user.

    The target row lock serializes grants and revocations for the same user.
    The nested transaction keeps a database uniqueness race recoverable without
    rolling back the caller's wider transaction.
    """

    user = _locked_active_target(db, user_id)
    _protect_superadmin(db, user_id)
    existing = _role(db, user_id, role)
    if existing is not None:
        return PlatformAccessMutation(user=user, changed=False, role_id=existing.id)
    if require_mapped_collaborator and not _has_active_primary_phone_mapping(db, user):
        raise PlatformAccessTargetUnmapped

    grant = PlatformRole(user_id=user_id, role=role)
    try:
        with db.begin_nested():
            db.add(grant)
            db.flush()
    except IntegrityError as exc:
        # The unique (user_id, role) constraint is the final concurrency
        # authority. A concurrent equivalent grant is an idempotent success.
        existing = _role(db, user_id, role)
        if existing is not None:
            return PlatformAccessMutation(user=user, changed=False, role_id=existing.id)
        raise PlatformAccessConflict from exc

    audit_after: dict[str, str | bool] = {"user_id": user_id, "role": role}
    if user_created:
        audit_after["user_created"] = True
    audit(
        db,
        actor_user_id=actor_user_id,
        action=f"platform.{role}.granted",
        resource_type="platform_role",
        resource_id=grant.id,
        after=audit_after,
    )
    return PlatformAccessMutation(user=user, changed=True, role_id=grant.id)


def revoke_platform_access(
    db: Session,
    *,
    actor_user_id: str,
    user_id: str,
    role: PlatformAccessRole,
) -> PlatformAccessMutation:
    """Idempotently revoke one manageable role without changing any identity."""

    user = _locked_target(db, user_id)
    _protect_superadmin(db, user_id)
    existing = _role(db, user_id, role)
    if existing is None:
        return PlatformAccessMutation(user=user, changed=False, role_id=None)

    role_id = existing.id
    db.delete(existing)
    db.flush()
    audit(
        db,
        actor_user_id=actor_user_id,
        action=f"platform.{role}.revoked",
        resource_type="platform_role",
        resource_id=role_id,
        before={"user_id": user_id, "role": role},
    )
    return PlatformAccessMutation(user=user, changed=True, role_id=role_id)


def _locked_active_target(db: Session, user_id: str) -> User:
    user = _locked_target(db, user_id)
    if user.status != "active":
        raise PlatformAccessTargetInactive
    return user


def _locked_target(db: Session, user_id: str) -> User:
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise PlatformAccessTargetNotFound
    return user


def _protect_superadmin(db: Session, user_id: str) -> None:
    if _role(db, user_id, "superadmin") is not None:
        raise ProtectedSuperadmin


def _has_active_primary_phone_mapping(db: Session, user: User) -> bool:
    """Return whether the user's login phone is approved for an active workspace."""

    return (
        db.scalar(
            select(PhoneIdentity.id)
            .join(
                TenantMembership,
                (TenantMembership.tenant_id == PhoneIdentity.tenant_id)
                & (TenantMembership.user_id == PhoneIdentity.user_id),
            )
            .join(Tenant, Tenant.id == PhoneIdentity.tenant_id)
            .where(
                PhoneIdentity.user_id == user.id,
                PhoneIdentity.phone_e164 == user.primary_phone_e164,
                PhoneIdentity.status == "approved",
                PhoneIdentity.opt_out.is_(False),
                TenantMembership.status == "active",
                Tenant.status == "active",
            )
            .limit(1)
        )
        is not None
    )


def _role(db: Session, user_id: str, role: str) -> PlatformRole | None:
    return db.scalar(
        select(PlatformRole).where(
            PlatformRole.user_id == user_id,
            PlatformRole.role == role,
        )
    )
