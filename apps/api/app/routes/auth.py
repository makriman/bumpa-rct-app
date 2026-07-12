from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, extract_token, get_principal
from app.core.security import create_access_token, issue_otp, revoke_token, verify_otp
from app.db.models import PlatformRole, TenantMembership
from app.db.session import get_db
from app.providers.local import LocalMessagingProvider
from app.schemas import AuthResponse, MessageResponse, OtpRequest, OtpRequested, OtpVerify

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/request-otp", response_model=OtpRequested, status_code=202)
def request_otp(
    payload: OtpRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OtpRequested:
    _otp, code = issue_otp(db, payload.phone_e164, settings)
    LocalMessagingProvider().send_otp(payload.phone_e164, code)
    return OtpRequested(
        expires_in_seconds=settings.otp_ttl_minutes * 60,
        dev_code=code if settings.is_local and settings.expose_local_otp else None,
    )


@router.post("/verify-otp", response_model=AuthResponse)
def verify_code(
    payload: OtpVerify,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    user = verify_otp(db, payload.phone_e164, payload.code, settings)
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


@router.post("/logout", response_model=MessageResponse)
def logout(
    response: Response,
    token: str = Depends(extract_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    revoke_token(db, token, settings)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return MessageResponse(message="Logged out")


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    memberships = db.scalars(
        select(TenantMembership).where(TenantMembership.user_id == principal.user.id)
    ).all()
    roles = db.scalars(
        select(PlatformRole.role).where(PlatformRole.user_id == principal.user.id)
    ).all()
    return {
        "user": {
            "id": principal.user.id,
            "name": principal.user.name,
            "email": principal.user.email,
            "phone_e164": principal.user.primary_phone_e164,
        },
        "platform_roles": list(roles),
        "memberships": [
            {"id": item.id, "tenant_id": item.tenant_id, "role": item.role, "status": item.status}
            for item in memberships
        ],
        "current_tenant_id": principal.tenant.id if principal.tenant else None,
    }
