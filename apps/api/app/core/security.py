from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from fastapi import HTTPException, status
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import secret_hash, secure_equals
from app.core.time import utcnow
from app.db.models import (
    AuthSession,
    OtpSession,
    PhoneIdentity,
    PlatformRole,
    Tenant,
    TenantMembership,
    User,
)

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(value: str) -> str:
    phone = re.sub(r"[\s()-]", "", value)
    if not E164_RE.fullmatch(phone):
        raise HTTPException(status_code=422, detail="Phone must be in E.164 format")
    return phone


def find_login_eligible_user(db: Session, phone: str) -> User | None:
    """Resolve an identity that is currently approved for WhatsApp authentication."""

    normalized = normalize_phone(phone)
    active_tenant_identity = exists(
        select(PhoneIdentity.id)
        .join(
            TenantMembership,
            and_(
                TenantMembership.tenant_id == PhoneIdentity.tenant_id,
                TenantMembership.user_id == PhoneIdentity.user_id,
            ),
        )
        .join(Tenant, Tenant.id == PhoneIdentity.tenant_id)
        .where(
            PhoneIdentity.user_id == User.id,
            PhoneIdentity.phone_e164 == normalized,
            PhoneIdentity.status == "approved",
            PhoneIdentity.opt_out.is_(False),
            TenantMembership.status == "active",
            Tenant.status == "active",
        )
    )
    approved_platform_role = exists(
        select(PlatformRole.id).where(
            PlatformRole.user_id == User.id,
            PlatformRole.role.in_(("operator", "researcher", "superadmin")),
        )
    )
    opted_out_identity = exists(
        select(PhoneIdentity.id).where(
            PhoneIdentity.user_id == User.id,
            PhoneIdentity.phone_e164 == normalized,
            PhoneIdentity.opt_out.is_(True),
        )
    )
    return db.scalar(
        select(User).where(
            User.primary_phone_e164 == normalized,
            User.status == "active",
            or_(active_tenant_identity, approved_platform_role),
            ~opted_out_identity,
        )
    )


def find_mapped_login_user(db: Session, phone: str) -> User | None:
    """Resolve only an active user with an approved, active tenant mapping."""

    normalized = normalize_phone(phone)
    mapped_identity = exists(
        select(PhoneIdentity.id)
        .join(
            TenantMembership,
            and_(
                TenantMembership.tenant_id == PhoneIdentity.tenant_id,
                TenantMembership.user_id == PhoneIdentity.user_id,
            ),
        )
        .join(Tenant, Tenant.id == PhoneIdentity.tenant_id)
        .where(
            PhoneIdentity.user_id == User.id,
            PhoneIdentity.phone_e164 == normalized,
            PhoneIdentity.status == "approved",
            PhoneIdentity.opt_out.is_(False),
            TenantMembership.status == "active",
            Tenant.status == "active",
        )
    )
    return db.scalar(
        select(User).where(
            User.primary_phone_e164 == normalized,
            User.status == "active",
            mapped_identity,
        )
    )


def issue_temporary_web_pin_challenge(
    db: Session,
    phone: str,
    settings: Settings,
) -> None:
    """Create or retain a short-lived provider-free challenge for a mapped identity."""

    normalized = normalize_phone(phone)
    now = utcnow()
    latest = db.scalar(
        select(OtpSession)
        .where(
            OtpSession.phone_e164 == normalized,
            OtpSession.purpose == "temporary_web_pin",
            OtpSession.consumed_at.is_(None),
        )
        .order_by(OtpSession.created_at.desc())
    )
    if latest and _aware_timestamp(latest.expires_at) >= now.timestamp():
        return
    if latest:
        latest.consumed_at = now
        db.flush()
    challenge_nonce = str(uuid4())
    db.add(
        OtpSession(
            phone_e164=normalized,
            code_hash=secret_hash(
                f"web-pin-challenge:{normalized}:{challenge_nonce}",
                settings.otp_secret,
            ),
            purpose="temporary_web_pin",
            expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(OtpSession)
            .where(
                OtpSession.phone_e164 == normalized,
                OtpSession.purpose == "temporary_web_pin",
                OtpSession.consumed_at.is_(None),
            )
            .order_by(OtpSession.created_at.desc())
        )
        if concurrent and _aware_timestamp(concurrent.expires_at) >= now.timestamp():
            return
        raise


def verify_temporary_web_pin(
    db: Session,
    phone: str,
    code: str,
    settings: Settings,
) -> User | None:
    """Return a strictly mapped user or a single privacy-preserving denial value."""

    normalized = normalize_phone(phone)
    candidate = secret_hash(f"web-login-pin:{code}", settings.otp_secret)
    pin_matches = secure_equals(settings.effective_temporary_web_pin_verifier, candidate)
    now = utcnow()
    challenge = db.scalar(
        select(OtpSession)
        .where(
            OtpSession.phone_e164 == normalized,
            OtpSession.purpose == "temporary_web_pin",
            OtpSession.consumed_at.is_(None),
        )
        .order_by(OtpSession.created_at.desc())
        .with_for_update()
    )
    challenge_active = bool(
        challenge
        and _aware_timestamp(challenge.expires_at) >= now.timestamp()
        and challenge.attempts < settings.otp_max_attempts
    )
    if not pin_matches or not challenge_active:
        if challenge and _aware_timestamp(challenge.expires_at) >= now.timestamp():
            challenge.attempts += 1
            if challenge.attempts >= settings.otp_max_attempts:
                challenge.consumed_at = now
        return None
    user = find_mapped_login_user(db, normalized)
    if challenge:
        challenge.consumed_at = now
    return user


def issue_otp(db: Session, phone: str, settings: Settings) -> tuple[OtpSession, str]:
    normalized = normalize_phone(phone)
    now = utcnow()
    latest = db.scalar(
        select(OtpSession)
        .where(OtpSession.phone_e164 == normalized)
        .order_by(OtpSession.created_at.desc())
    )
    if latest:
        elapsed = now.timestamp() - _aware_timestamp(latest.created_at)
        if elapsed < settings.otp_request_cooldown_seconds and latest.consumed_at is None:
            raise HTTPException(
                status_code=429, detail="Please wait before requesting another code"
            )
    code = (
        settings.effective_local_otp_code
        if settings.is_local
        else f"{int.from_bytes(uuid4().bytes[:4]):010d}"[-6:]
    )
    otp = OtpSession(
        phone_e164=normalized,
        code_hash=secret_hash(f"{normalized}:{code}", settings.otp_secret),
        purpose="login",
        expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
    )
    db.add(otp)
    db.commit()
    db.refresh(otp)
    return otp, code


def verify_otp(db: Session, phone: str, code: str, settings: Settings) -> User:
    normalized = normalize_phone(phone)
    otp = db.scalar(
        select(OtpSession)
        .where(OtpSession.phone_e164 == normalized, OtpSession.consumed_at.is_(None))
        .order_by(OtpSession.created_at.desc())
    )
    generic = HTTPException(status_code=401, detail="Invalid or expired code")
    if not otp or _aware_timestamp(otp.expires_at) < utcnow().timestamp():
        raise generic
    if otp.attempts >= settings.otp_max_attempts:
        raise HTTPException(status_code=423, detail="Code is locked")
    expected = secret_hash(f"{normalized}:{code}", settings.otp_secret)
    if not secure_equals(otp.code_hash, expected):
        otp.attempts += 1
        db.commit()
        if otp.attempts >= settings.otp_max_attempts:
            raise HTTPException(status_code=423, detail="Code is locked")
        raise generic
    user = find_login_eligible_user(db, normalized)
    if not user:
        otp.consumed_at = utcnow()
        db.commit()
        raise HTTPException(status_code=403, detail="This phone number is not approved")
    otp.consumed_at = utcnow()
    db.commit()
    return user


def create_access_token(db: Session, user: User, settings: Settings) -> tuple[str, AuthSession]:
    now = utcnow()
    expires = now + timedelta(minutes=settings.access_token_minutes)
    jti = str(uuid4())
    payload = {"sub": user.id, "jti": jti, "iat": now, "exp": expires, "type": "access"}
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    auth_session = AuthSession(
        user_id=user.id,
        token_jti_hash=hashlib.sha256(jti.encode()).hexdigest(),
        expires_at=expires,
    )
    db.add(auth_session)
    db.commit()
    db.refresh(auth_session)
    return token, auth_session


def decode_access_token(db: Session, token: str, settings: Settings) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise unauthorized
        user_id, jti = payload["sub"], payload["jti"]
    except (jwt.PyJWTError, KeyError) as exc:
        raise unauthorized from exc
    token_hash = hashlib.sha256(jti.encode()).hexdigest()
    auth_session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.token_jti_hash == token_hash,
            AuthSession.revoked_at.is_(None),
        )
    )
    if not auth_session or _aware_timestamp(auth_session.expires_at) < utcnow().timestamp():
        raise unauthorized
    user = db.get(User, user_id)
    eligible_user: User | None = None
    if user:
        resolver = (
            find_mapped_login_user
            if settings.auth_login_mode == "temporary_static_pin"
            else find_login_eligible_user
        )
        eligible_user = resolver(db, user.primary_phone_e164)
    if not user or user.status != "active" or eligible_user is None:
        raise unauthorized
    return user


def revoke_token(db: Session, token: str, settings: Settings) -> None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        token_hash = hashlib.sha256(payload["jti"].encode()).hexdigest()
    except (jwt.PyJWTError, KeyError):
        return
    auth_session = db.scalar(select(AuthSession).where(AuthSession.token_jti_hash == token_hash))
    if auth_session and auth_session.revoked_at is None:
        auth_session.revoked_at = utcnow()
        db.commit()


def revoke_other_tokens(
    db: Session,
    *,
    user_id: str,
    current_token: str,
    settings: Settings,
) -> int:
    try:
        payload = jwt.decode(current_token, settings.jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "access" or payload.get("sub") != user_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        current_hash = hashlib.sha256(payload["jti"].encode()).hexdigest()
    except (jwt.PyJWTError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Authentication required") from exc
    revoked_at = utcnow()
    sessions = db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > revoked_at,
            AuthSession.token_jti_hash != current_hash,
        )
    ).all()
    for session in sessions:
        session.revoked_at = revoked_at
    return len(sessions)


def _aware_timestamp(value: object) -> float:
    if not isinstance(value, datetime):
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
