from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, BinaryIO, TextIO

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import FieldCipher
from app.db.models import (
    BumpaConnection,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal, set_security_context
from app.services.audit import audit

MAX_STDIN_BYTES = 65_536
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
BUSINESS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_min_length=1, strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_non_normalized_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and (
            value != value.strip() or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("String values must be normalized and contain no control characters")
        return value


class TenantInput(StrictInput):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=200)


class OwnerInput(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    phone_e164: str

    @field_validator("phone_e164")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not E164_RE.fullmatch(value):
            raise ValueError("Phone must use normalized E.164 format")
        return value


class OperatorInput(StrictInput):
    phone_e164: str
    name: str = Field(default="Bootstrap Operator", min_length=1, max_length=200)
    bootstrap_if_missing: bool = False

    @field_validator("phone_e164")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not E164_RE.fullmatch(value):
            raise ValueError("Phone must use normalized E.164 format")
        return value


class BumpaInput(StrictInput):
    api_key: str = Field(min_length=8, max_length=500)
    business_id: str = Field(min_length=1, max_length=160)

    @field_validator("business_id")
    @classmethod
    def validate_business_id(cls, value: str) -> str:
        if not BUSINESS_ID_RE.fullmatch(value):
            raise ValueError("Bumpa business ID contains unsupported characters")
        return value


class OnboardingBundle(StrictInput):
    tenant: TenantInput
    owner: OwnerInput
    operator: OperatorInput
    bumpa: BumpaInput
    apply: bool = False
    confirmation: str | None = None

    @model_validator(mode="after")
    def validate_confirmation(self) -> OnboardingBundle:
        expected = f"APPLY {self.tenant.slug}"
        if self.apply and self.confirmation != expected:
            raise ValueError("Apply requires the tenant-specific confirmation phrase")
        if not self.apply and self.confirmation is not None:
            raise ValueError("Dry-run input must omit confirmation")
        return self


class OnboardingError(RuntimeError):
    def __init__(self, code: str, *, fields: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields or []


@dataclass
class OnboardingResult:
    ids: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "audit_rows": 0,
            "applied": 0,
            "dry_run": 1,
        }
    )

    def public_dict(self) -> dict[str, dict[str, str] | dict[str, int]]:
        return {"ids": self.ids, "counts": self.counts}


def parse_stdin_bundle(stream: BinaryIO) -> OnboardingBundle:
    raw = stream.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise OnboardingError("input_too_large")
    if not raw.strip():
        raise OnboardingError("empty_input")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise OnboardingError("duplicate_json_key")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicates)
    except OnboardingError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise OnboardingError("invalid_json") from None
    if not isinstance(payload, dict):
        raise OnboardingError("input_must_be_object")
    try:
        return OnboardingBundle.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        raise OnboardingError("validation_failed", fields=fields) from None


def onboard(
    db: Session, bundle: OnboardingBundle, *, field_encryption_key: str
) -> OnboardingResult:
    if len(field_encryption_key) < 24 or field_encryption_key.startswith("local-only"):
        raise OnboardingError("field_encryption_key_invalid")
    result = OnboardingResult()
    set_security_context(db, privileged=True)
    try:
        actor = _upsert_operator(db, bundle.operator, result)
        tenant = _upsert_tenant(db, bundle.tenant, actor.id, result)
        owner = _upsert_owner(db, tenant, bundle.owner, actor.id, result)
        membership = _upsert_membership(db, tenant, owner, actor.id, result)
        phone = _upsert_phone(db, tenant, owner, bundle.owner.phone_e164, actor.id, result)
        connection = _upsert_bumpa(
            db,
            tenant,
            bundle.bumpa,
            actor.id,
            FieldCipher(field_encryption_key),
            result,
        )
        audit(
            db,
            actor_user_id=actor.id,
            tenant_id=tenant.id,
            action="tenant.onboarding.applied" if bundle.apply else "tenant.onboarding.dry_run",
            resource_type="tenant",
            resource_id=tenant.id,
            after={
                "created": result.counts["created"],
                "updated": result.counts["updated"],
                "unchanged": result.counts["unchanged"],
            },
        )
        result.counts["audit_rows"] += 1
        db.flush()
        if bundle.apply:
            db.commit()
            result.counts["applied"] = 1
            result.counts["dry_run"] = 0
            result.ids = {
                "operator_user_id": actor.id,
                "tenant_id": tenant.id,
                "owner_user_id": owner.id,
                "membership_id": membership.id,
                "phone_identity_id": phone.id,
                "bumpa_connection_id": connection.id,
            }
        else:
            db.rollback()
        return result
    except OnboardingError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise OnboardingError("conflicting_existing_record") from None
    except SQLAlchemyError:
        db.rollback()
        raise OnboardingError("database_failure") from None


def _mark(result: OnboardingResult, state: str) -> None:
    result.counts[state] += 1


def _record_audit(
    db: Session,
    result: OnboardingResult,
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    tenant_id: str | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    audit(
        db,
        actor_user_id=actor_id,
        tenant_id=tenant_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        after=after,
    )
    result.counts["audit_rows"] += 1


def _upsert_operator(db: Session, data: OperatorInput, result: OnboardingResult) -> User:
    user = db.scalar(select(User).where(User.primary_phone_e164 == data.phone_e164))
    created_user = False
    if not user:
        if not data.bootstrap_if_missing:
            raise OnboardingError("operator_not_found")
        user = User(name=data.name, primary_phone_e164=data.phone_e164, status="active")
        db.add(user)
        db.flush()
        created_user = True
        _mark(result, "created")
    elif user.status != "active":
        raise OnboardingError("operator_inactive")

    role = db.scalar(
        select(PlatformRole).where(
            PlatformRole.user_id == user.id,
            PlatformRole.role.in_(("operator", "superadmin")),
        )
    )
    if not role:
        if not data.bootstrap_if_missing:
            raise OnboardingError("operator_role_required")
        role = PlatformRole(user_id=user.id, role="operator")
        db.add(role)
        db.flush()
        _mark(result, "created")
        _record_audit(
            db,
            result,
            actor_id=user.id,
            action="platform.operator.bootstrapped",
            resource_type="platform_role",
            resource_id=role.id,
            after={"role": "operator", "user_created": created_user},
        )
    else:
        _mark(result, "unchanged")
    return user


def _upsert_tenant(
    db: Session, data: TenantInput, actor_id: str, result: OnboardingResult
) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == data.slug))
    if not tenant:
        tenant = Tenant(slug=data.slug, name=data.name, status="active")
        db.add(tenant)
        db.flush()
        _mark(result, "created")
        action = "tenant.created"
    elif tenant.name != data.name or tenant.status != "active":
        tenant.name = data.name
        tenant.status = "active"
        _mark(result, "updated")
        action = "tenant.updated"
    else:
        _mark(result, "unchanged")
        return tenant
    _record_audit(
        db,
        result,
        actor_id=actor_id,
        tenant_id=tenant.id,
        action=action,
        resource_type="tenant",
        resource_id=tenant.id,
        after={"status": "active"},
    )
    return tenant


def _upsert_owner(
    db: Session,
    tenant: Tenant,
    data: OwnerInput,
    actor_id: str,
    result: OnboardingResult,
) -> User:
    user = db.scalar(select(User).where(User.primary_phone_e164 == data.phone_e164))
    if not user:
        user = User(name=data.name, primary_phone_e164=data.phone_e164, status="active")
        db.add(user)
        db.flush()
        _mark(result, "created")
        action = "tenant.owner.created"
    elif user.name != data.name or user.status != "active":
        user.name = data.name
        user.status = "active"
        _mark(result, "updated")
        action = "tenant.owner.updated"
    else:
        _mark(result, "unchanged")
        return user
    _record_audit(
        db,
        result,
        actor_id=actor_id,
        tenant_id=tenant.id,
        action=action,
        resource_type="user",
        resource_id=user.id,
        after={"status": "active"},
    )
    return user


def _upsert_membership(
    db: Session, tenant: Tenant, owner: User, actor_id: str, result: OnboardingResult
) -> TenantMembership:
    conflicting_owner = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.role == "owner",
            TenantMembership.status == "active",
            TenantMembership.user_id != owner.id,
        )
    )
    if conflicting_owner:
        raise OnboardingError("tenant_owner_conflict")
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == owner.id,
        )
    )
    if not membership:
        membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=owner.id,
            role="owner",
            status="active",
        )
        db.add(membership)
        db.flush()
        _mark(result, "created")
        action = "tenant.owner_membership.created"
    elif membership.role != "owner" or membership.status != "active":
        membership.role = "owner"
        membership.status = "active"
        _mark(result, "updated")
        action = "tenant.owner_membership.updated"
    else:
        _mark(result, "unchanged")
        return membership
    _record_audit(
        db,
        result,
        actor_id=actor_id,
        tenant_id=tenant.id,
        action=action,
        resource_type="membership",
        resource_id=membership.id,
        after={"role": "owner", "status": "active", "user_id": owner.id},
    )
    return membership


def _upsert_phone(
    db: Session,
    tenant: Tenant,
    owner: User,
    phone_e164: str,
    actor_id: str,
    result: OnboardingResult,
) -> PhoneIdentity:
    identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone_e164))
    if identity and (identity.tenant_id != tenant.id or identity.user_id != owner.id):
        raise OnboardingError("phone_identity_conflict")
    if not identity:
        identity = PhoneIdentity(
            tenant_id=tenant.id,
            user_id=owner.id,
            phone_e164=phone_e164,
            label="Owner",
            status="approved",
        )
        db.add(identity)
        db.flush()
        _mark(result, "created")
        action = "phone.approved"
    elif identity.status != "approved" or identity.opt_out:
        identity.status = "approved"
        identity.opt_out = False
        _mark(result, "updated")
        action = "phone.reapproved"
    else:
        _mark(result, "unchanged")
        return identity
    _record_audit(
        db,
        result,
        actor_id=actor_id,
        tenant_id=tenant.id,
        action=action,
        resource_type="phone_identity",
        resource_id=identity.id,
        after={"status": "approved", "user_id": owner.id},
    )
    return identity


def _upsert_bumpa(
    db: Session,
    tenant: Tenant,
    data: BumpaInput,
    actor_id: str,
    cipher: FieldCipher,
    result: OnboardingResult,
) -> BumpaConnection:
    connection = db.scalar(select(BumpaConnection).where(BumpaConnection.tenant_id == tenant.id))
    if not connection:
        connection = BumpaConnection(
            tenant_id=tenant.id,
            encrypted_api_key=cipher.encrypt(data.api_key),
            scope_type="business_id",
            scope_id=data.business_id,
            provider="bumpa",
            status="active",
        )
        db.add(connection)
        db.flush()
        _mark(result, "created")
        action = "tenant.bumpa_connection.created"
    else:
        try:
            same_key = cipher.decrypt(connection.encrypted_api_key) == data.api_key
        except (ValueError, UnicodeDecodeError):
            same_key = False
        if (
            same_key
            and connection.scope_type == "business_id"
            and connection.scope_id == data.business_id
            and connection.provider == "bumpa"
            and connection.status == "active"
        ):
            _mark(result, "unchanged")
            return connection
        connection.encrypted_api_key = cipher.encrypt(data.api_key)
        connection.scope_type = "business_id"
        connection.scope_id = data.business_id
        connection.provider = "bumpa"
        connection.status = "active"
        _mark(result, "updated")
        action = "tenant.bumpa_connection.updated"
    _record_audit(
        db,
        result,
        actor_id=actor_id,
        tenant_id=tenant.id,
        action=action,
        resource_type="bumpa_connection",
        resource_id=connection.id,
        after={"provider": "bumpa", "scope_type": "business_id", "status": "active"},
    )
    return connection


def _safe_error(error: OnboardingError) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": error.code}
    if error.fields:
        payload["fields"] = error.fields
    return payload


def main(
    *,
    stdin: BinaryIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    input_stream = stdin or sys.stdin.buffer
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    if len(sys.argv) > 1 and stdin is None:
        error_stream.write(json.dumps({"error": "arguments_not_supported"}) + "\n")
        return 2
    try:
        bundle = parse_stdin_bundle(input_stream)
        settings = get_settings()
        with SessionLocal() as db:
            result = onboard(db, bundle, field_encryption_key=settings.field_encryption_key)
    except OnboardingError as exc:
        error_stream.write(json.dumps(_safe_error(exc), sort_keys=True) + "\n")
        return (
            2
            if exc.code
            in {
                "arguments_not_supported",
                "duplicate_json_key",
                "empty_input",
                "input_must_be_object",
                "input_too_large",
                "invalid_json",
                "validation_failed",
            }
            else 1
        )
    except (OSError, ValidationError, ValueError):
        error_stream.write(json.dumps({"error": "configuration_invalid"}) + "\n")
        return 1
    output_stream.write(json.dumps(result.public_dict(), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
