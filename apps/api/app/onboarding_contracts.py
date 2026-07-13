from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

OnboardingStatus = Literal["in_progress", "attention_required", "completed"]
OnboardingStep = Literal[
    "owner",
    "phone",
    "bumpa",
    "initial_sync",
    "hermes",
    "review",
    "completed",
]

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


class OnboardingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_unsafe_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and (
            value != value.strip() or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Strings must be normalized and contain no control characters")
        return value


class OnboardingStartRequest(OnboardingInput):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=200)
    business_category: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    city: str | None = Field(default=None, max_length=120)
    timezone: str = Field(default="Africa/Lagos", min_length=1, max_length=64)
    currency_code: str = Field(default="NGN", pattern=r"^[A-Z]{3}$")


class OnboardingOwnerRequest(OnboardingInput):
    name: str = Field(min_length=1, max_length=200)
    phone_e164: str
    email: EmailStr | None = None

    @field_validator("phone_e164")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if _E164.fullmatch(value) is None:
            raise ValueError("Phone must use normalized E.164 format")
        return value


class OnboardingPhoneRequest(OnboardingInput):
    confirmation: Literal["approve"]
    label: str = Field(default="Owner", min_length=1, max_length=80)


class OnboardingBumpaRequest(OnboardingInput):
    api_key: SecretStr = Field(min_length=8, max_length=500)
    provider: Literal["bumpa"] = "bumpa"
    scope_type: Literal["business_id", "location_id"] = "business_id"
    scope_id: str = Field(min_length=1, max_length=160)

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        if _SCOPE_ID.fullmatch(value) is None:
            raise ValueError("Bumpa scope ID contains unsupported characters")
        return value


class OnboardingInitialSyncRequest(OnboardingInput):
    date_from: date
    date_to: date

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def parse_iso_date(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        return value

    @field_validator("date_to")
    @classmethod
    def validate_date_to(cls, value: date, info: Any) -> date:
        date_from = info.data.get("date_from")
        if isinstance(date_from, date) and (value < date_from or (value - date_from).days > 366):
            raise ValueError("Initial sync date range is invalid")
        return value


class OnboardingInitialSyncAcceptRequest(OnboardingInput):
    confirmation: Literal["accept"]


class OnboardingHermesRequest(OnboardingInput):
    confirmation: Literal["provision"]


class OnboardingCompleteRequest(OnboardingInput):
    confirmation: Literal["activate"]


class OnboardingTenantView(BaseModel):
    id: str
    slug: str
    name: str
    status: str


class OnboardingOwnerView(BaseModel):
    user_id: str
    membership_id: str
    name: str | None
    email_masked: str | None
    status: str


class OnboardingPhoneView(BaseModel):
    identity_id: str
    phone_masked: str
    label: str | None
    status: str
    opt_out: bool


class OnboardingBumpaView(BaseModel):
    connection_id: str
    provider: str
    scope_type: str
    scope_id_last4: str
    status: str


class OnboardingInitialSyncView(BaseModel):
    attempt: int
    requested_from: date
    requested_to: date
    job_id: str
    job_status: str
    sync_run_id: str | None
    sync_status: str | None
    completion_quality: str | None
    orders_availability: str | None
    orders_count: int | None


class OnboardingHermesView(BaseModel):
    profile_id: str
    profile_name: str
    provider: str
    api_port: int | None
    status: str


class OnboardingFailureView(BaseModel):
    code: str
    step: OnboardingStep
    retryable: bool
    at: datetime


class OnboardingView(BaseModel):
    id: str
    tenant_id: str
    status: OnboardingStatus
    current_step: OnboardingStep
    revision: int
    tenant: OnboardingTenantView
    owner: OnboardingOwnerView | None
    phone: OnboardingPhoneView | None
    bumpa: OnboardingBumpaView | None
    initial_sync: OnboardingInitialSyncView | None
    hermes: OnboardingHermesView | None
    failure: OnboardingFailureView | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class OnboardingMutation:
    view: OnboardingView
    created: bool = False
    replayed: bool = False


class OnboardingError(RuntimeError):
    """Safe domain error; code is suitable for an API error response and audit."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
