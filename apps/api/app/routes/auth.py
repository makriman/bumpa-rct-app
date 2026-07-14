import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import secret_hash
from app.core.dependencies import Principal, extract_token, get_principal
from app.core.rate_limit import enforce_auth_rate_limit
from app.core.security import (
    create_access_token,
    find_login_eligible_user,
    find_mapped_login_user,
    issue_otp,
    issue_temporary_web_pin_challenge,
    normalize_phone,
    revoke_other_tokens,
    revoke_token,
    verify_otp,
    verify_temporary_web_pin,
)
from app.core.time import utcnow
from app.db.models import User
from app.db.session import get_db, set_security_context
from app.providers.diagnostics import provider_failure_log_extra
from app.providers.local import LocalMessagingProvider
from app.providers.meta import MetaProviderError, MetaWhatsAppClient
from app.schemas import (
    AuthResponse,
    MessageResponse,
    OtpRequest,
    OtpRequested,
    OtpVerify,
    SessionsRevoked,
)
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("bumpabestie.providers")


@router.post("/request-otp", response_model=OtpRequested, status_code=202)
def request_otp(
    payload: OtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OtpRequested:
    if settings.auth_login_mode == "disabled":
        raise HTTPException(
            status_code=503,
            detail="Web authentication is not configured",
        )
    if settings.auth_login_mode == "whatsapp_otp" and settings.whatsapp_backend == "disabled":
        raise HTTPException(
            status_code=503,
            detail="WhatsApp OTP delivery is not configured",
        )
    phone_e164 = normalize_phone(payload.phone_e164)
    enforce_auth_rate_limit(request, phone_e164=phone_e164, operation="request", settings=settings)
    if settings.auth_login_mode == "temporary_static_pin":
        _ensure_temporary_web_pin_active(settings)
        # Eligibility is intentionally not reflected in the response. An active
        # mapped identity receives a provider-free, short-lived challenge only.
        set_security_context(db, privileged=True)
        if find_mapped_login_user(db, phone_e164) is not None:
            issue_temporary_web_pin_challenge(db, phone_e164, settings)
        return OtpRequested(
            status="accepted",
            expires_in_seconds=settings.otp_ttl_minutes * 60,
            delivery="web_pin",
        )
    # Eligibility spans tenant-scoped identity/membership rows before a tenant
    # principal exists. Restrict this privileged context to the bounded auth
    # transaction, then resolve only the submitted normalized phone.
    set_security_context(db, privileged=True)
    if find_login_eligible_user(db, phone_e164) is None:
        # Preserve the same public response shape without creating a credential
        # or spending a provider message on an unknown/inactive destination.
        return OtpRequested(expires_in_seconds=settings.otp_ttl_minutes * 60)
    otp, code = issue_otp(db, phone_e164, settings)
    try:
        if settings.whatsapp_backend == "meta":
            MetaWhatsAppClient.from_settings(settings).send_otp(phone_e164, code)
        else:
            LocalMessagingProvider().send_otp(phone_e164, code)
    except MetaProviderError as exc:
        otp.consumed_at = utcnow()
        db.commit()
        logger.warning(
            "meta_otp_delivery_failed",
            extra=provider_failure_log_extra(
                provider="meta",
                operation="otp_delivery",
                category=exc.category,
                retryable=exc.retryable,
                http_status=exc.http_status,
                code=exc.provider_code,
                request_id_hash=exc.request_id_hash,
                retry_after_seconds=exc.retry_after_seconds,
            ),
        )
        raise HTTPException(
            status_code=503 if exc.retryable else 502,
            detail=(
                "WhatsApp OTP delivery is temporarily unavailable"
                if exc.retryable
                else "WhatsApp rejected OTP delivery"
            ),
        ) from exc
    except ValueError as exc:
        otp.consumed_at = utcnow()
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="WhatsApp OTP delivery is not configured",
        ) from exc
    return OtpRequested(
        expires_in_seconds=settings.otp_ttl_minutes * 60,
        dev_code=code if settings.is_local and settings.expose_local_otp else None,
    )


@router.post("/verify-otp", response_model=AuthResponse)
def verify_code(
    payload: OtpVerify,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    phone_e164 = normalize_phone(payload.phone_e164)
    enforce_auth_rate_limit(request, phone_e164=phone_e164, operation="verify", settings=settings)
    if settings.auth_login_mode == "disabled":
        raise HTTPException(status_code=503, detail="Web authentication is not configured")
    if settings.auth_login_mode == "temporary_static_pin":
        _ensure_temporary_web_pin_active(settings)
    set_security_context(db, privileged=True)
    if settings.auth_login_mode == "temporary_static_pin":
        user = _verify_temporary_static_pin(db, phone_e164, payload.code, settings)
    elif settings.auth_login_mode == "whatsapp_otp":
        user = verify_otp(db, phone_e164, payload.code, settings)
    else:  # pragma: no cover - exhaustive guard for future modes
        raise RuntimeError("Unsupported authentication mode")
    token, _session = create_access_token(db, user, settings)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_minutes * 60,
        path="/",
        domain=settings.cookie_domain,
    )
    return AuthResponse(
        access_token=token,
        user={"id": user.id, "name": user.name, "phone_e164": user.primary_phone_e164},
    )


def _verify_temporary_static_pin(
    db: Session,
    phone_e164: str,
    submitted_code: str,
    settings: Settings,
) -> User:
    """Verify the temporary shared credential without making identity an oracle."""

    user = verify_temporary_web_pin(db, phone_e164, submitted_code, settings)
    phone_reference = secret_hash(f"auth-audit:phone:{phone_e164}", settings.otp_secret)
    if user is None:
        audit(
            db,
            actor_user_id=None,
            action="auth.temporary_static_pin.denied",
            resource_type="phone_identity",
            resource_id=phone_reference,
            after={"outcome": "invalid_credentials"},
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    audit(
        db,
        actor_user_id=user.id,
        action="auth.temporary_static_pin.verified",
        resource_type="phone_identity",
        resource_id=phone_reference,
        after={"outcome": "success"},
    )
    return user


def _ensure_temporary_web_pin_active(settings: Settings) -> None:
    expires_at = settings.temporary_web_pin_expires_at
    if expires_at is None or utcnow() >= expires_at:
        raise HTTPException(
            status_code=503,
            detail="Temporary web authentication is unavailable",
        )


@router.post("/logout", response_model=MessageResponse)
def logout(
    response: Response,
    token: str = Depends(extract_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    revoke_token(db, token, settings)
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return MessageResponse(message="Logged out")


@router.post("/logout-others", response_model=SessionsRevoked)
def logout_other_sessions(
    token: str = Depends(extract_token),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionsRevoked:
    revoked = revoke_other_tokens(
        db,
        user_id=principal.user.id,
        current_token=token,
        settings=settings,
    )
    audit(
        db,
        actor_user_id=principal.user.id,
        tenant_id=principal.tenant.id if principal.tenant else None,
        action="auth.sessions.revoked_others",
        resource_type="auth_session",
        after={"revoked_sessions": revoked},
    )
    db.commit()
    return SessionsRevoked(
        message="Other sessions signed out",
        revoked_sessions=revoked,
    )


@router.get("/me")
def me(principal: Principal = Depends(get_principal)) -> dict:
    # Authentication resolves this complete user-owned snapshot while the session
    # is privileged, before ordinary users are narrowed to one tenant by RLS.
    # Re-querying here would silently hide their other authorized memberships.
    return {
        "user": {
            "id": principal.user.id,
            "name": principal.user.name,
            "email": principal.user.email,
            "phone_e164": principal.user.primary_phone_e164,
        },
        "platform_roles": sorted(principal.platform_roles),
        "memberships": [
            {"id": item.id, "tenant_id": item.tenant_id, "role": item.role, "status": item.status}
            for item in principal.memberships
        ],
        "current_tenant_id": principal.tenant.id if principal.tenant else None,
    }
