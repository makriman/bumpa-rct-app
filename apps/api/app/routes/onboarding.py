from __future__ import annotations

from typing import NoReturn, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_operator
from app.db.session import get_db
from app.onboarding_contracts import (
    OnboardingBumpaRequest,
    OnboardingCompleteRequest,
    OnboardingError,
    OnboardingHermesRequest,
    OnboardingInitialSyncAcceptRequest,
    OnboardingInitialSyncRequest,
    OnboardingOwnerRequest,
    OnboardingPhoneRequest,
    OnboardingStartRequest,
    OnboardingStatus,
    OnboardingView,
)
from app.services.onboarding import OnboardingService

router = APIRouter(prefix="/admin/onboardings", tags=["admin", "onboarding"])

_ERRORS: dict[str, tuple[int, str, bool]] = {
    "onboarding_not_found": (404, "Onboarding record was not found", False),
    "tenant_not_found": (404, "Tenant was not found", False),
    "revision_conflict": (
        status.HTTP_412_PRECONDITION_FAILED,
        "Onboarding changed; refresh and try again",
        True,
    ),
    "idempotency_conflict": (
        409,
        "Idempotency-Key was already used for different input",
        False,
    ),
    "invalid_step": (409, "This command is not valid for the current step", False),
    "initial_sync_not_ready": (409, "Initial sync is not ready for acceptance", True),
    "initial_sync_in_progress": (409, "Initial sync is still running", True),
    "initial_sync_retry_not_allowed": (
        409,
        "The current initial sync cannot be replaced",
        True,
    ),
    "tenant_slug_conflict": (409, "Tenant slug is already in use", False),
    "owner_conflict": (409, "Owner identity conflicts with an existing record", False),
    "phone_identity_conflict": (
        409,
        "WhatsApp number is already mapped to another tenant",
        False,
    ),
    "bumpa_verification_failed": (
        502,
        "Bumpa credential verification failed",
        True,
    ),
    "bumpa_unavailable": (503, "Bumpa is temporarily unavailable", True),
    "bumpa_connection_conflict": (
        409,
        "Bumpa connection conflicts with the onboarding record",
        False,
    ),
    "queue_unavailable": (503, "The background queue is unavailable", True),
    "hermes_provisioning_failed": (
        503,
        "Hermes provisioning did not become ready",
        True,
    ),
    "hermes_profile_conflict": (
        409,
        "Hermes profile conflicts with the onboarding record",
        False,
    ),
    "providers_not_ready": (
        503,
        "Required production providers are not ready",
        True,
    ),
    "completion_requirements_not_met": (
        409,
        "Onboarding requirements are not complete",
        True,
    ),
}


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "idempotency_key_required",
                "message": "Idempotency-Key is required",
                "retryable": False,
            },
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 120
        or any(ord(character) < 33 or ord(character) > 126 for character in normalized)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "idempotency_key_invalid",
                "message": "Idempotency-Key must contain 1 to 120 visible ASCII characters",
                "retryable": False,
            },
        )
    return normalized


def _expected_revision(value: str | None) -> int:
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "revision_required",
                "message": "If-Match revision is required",
                "retryable": True,
            },
        )
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = ""
    elif normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    if not normalized.isdigit() or len(normalized) > 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "revision_invalid",
                "message": "If-Match must contain a non-negative integer revision",
                "retryable": False,
            },
        )
    revision = int(normalized)
    if revision > 2_147_483_647:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "revision_invalid",
                "message": "If-Match revision is outside the supported range",
                "retryable": False,
            },
        )
    return revision


def _raise_domain(error: OnboardingError) -> NoReturn:
    error_status, message, retryable = _ERRORS.get(
        error.code,
        (status.HTTP_409_CONFLICT, "Onboarding command could not be applied", False),
    )
    raise HTTPException(
        status_code=error_status,
        detail={"code": error.code, "message": message, "retryable": retryable},
    ) from error


def _service(settings: Settings) -> OnboardingService:
    return OnboardingService(settings)


@router.post("", response_model=OnboardingView, status_code=status.HTTP_201_CREATED)
def start_onboarding(
    payload: OnboardingStartRequest,
    response: Response,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> OnboardingView:
    try:
        result = _service(settings).start(
            db,
            payload,
            actor_user_id=principal.user.id,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except OnboardingError as error:
        _raise_domain(error)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    response.headers["Location"] = f"/v1/admin/onboardings/{result.view.id}"
    return result.view


@router.get("", response_model=list[OnboardingView])
def list_onboardings(
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    onboarding_status: OnboardingStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[OnboardingView]:
    try:
        return _service(settings).list(
            db,
            status=onboarding_status,
            limit=limit,
        )
    except OnboardingError as error:
        _raise_domain(error)


@router.get("/{onboarding_id}", response_model=OnboardingView)
def get_onboarding(
    onboarding_id: str,
    _principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OnboardingView:
    try:
        return _service(settings).get(db, onboarding_id)
    except OnboardingError as error:
        _raise_domain(error)


def _mutate(
    method: str,
    *,
    db: Session,
    settings: Settings,
    onboarding_id: str,
    payload: object,
    principal: Principal,
    idempotency_key: str | None,
    if_match: str | None,
) -> OnboardingView:
    service = _service(settings)
    operation = getattr(service, method)
    try:
        result = operation(
            db,
            onboarding_id,
            payload,
            actor_user_id=principal.user.id,
            expected_revision=_expected_revision(if_match),
            idempotency_key=_idempotency_key(idempotency_key),
        )
        return cast(OnboardingView, result.view)
    except OnboardingError as error:
        _raise_domain(error)


@router.post("/{onboarding_id}/owner", response_model=OnboardingView)
def set_owner(
    onboarding_id: str,
    payload: OnboardingOwnerRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "set_owner",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post("/{onboarding_id}/phone", response_model=OnboardingView)
def approve_phone(
    onboarding_id: str,
    payload: OnboardingPhoneRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "approve_phone",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post("/{onboarding_id}/bumpa", response_model=OnboardingView)
def connect_bumpa(
    onboarding_id: str,
    payload: OnboardingBumpaRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "connect_bumpa",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post(
    "/{onboarding_id}/initial-sync",
    response_model=OnboardingView,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_initial_sync(
    onboarding_id: str,
    payload: OnboardingInitialSyncRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "trigger_initial_sync",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post("/{onboarding_id}/initial-sync/accept", response_model=OnboardingView)
def accept_initial_sync(
    onboarding_id: str,
    payload: OnboardingInitialSyncAcceptRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "accept_initial_sync",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post("/{onboarding_id}/hermes", response_model=OnboardingView)
def provision_hermes(
    onboarding_id: str,
    payload: OnboardingHermesRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "provision_hermes",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


@router.post("/{onboarding_id}/complete", response_model=OnboardingView)
def complete_onboarding(
    onboarding_id: str,
    payload: OnboardingCompleteRequest,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OnboardingView:
    return _mutate(
        "complete",
        db=db,
        settings=settings,
        onboarding_id=onboarding_id,
        payload=payload,
        principal=principal,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )
