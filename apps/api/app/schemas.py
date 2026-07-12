from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

ReportFormat = Literal["csv", "jsonl", "pdf"]
AsyncJobStatus = Literal[
    "pending",
    "queued",
    "running",
    "retry",
    "succeeded",
    "dead_letter",
    "cancelled",
]
AsyncJobReplayReason = Literal[
    "configuration_corrected",
    "dependency_recovered",
    "operator_verified_safe_retry",
    "transient_provider_recovered",
    "upstream_credentials_rotated",
]


def default_report_formats() -> list[ReportFormat]:
    return ["csv"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    message: str


class AsyncJobView(BaseModel):
    """Payload-free operational view of a durable asynchronous job."""

    id: str
    tenant_id: str | None
    kind: str
    status: AsyncJobStatus
    attempts: int
    max_attempts: int
    failure_category: str | None
    replayable: bool
    available_at: datetime
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AsyncJobReplayRequest(BaseModel):
    reason: AsyncJobReplayReason
    max_attempts: int | None = Field(default=None, ge=1, le=100)


class OtpRequest(BaseModel):
    phone_e164: str


class OtpRequested(BaseModel):
    status: Literal["sent"] = "sent"
    expires_in_seconds: int
    dev_code: str | None = None


class OtpVerify(BaseModel):
    phone_e164: str
    code: str = Field(min_length=6, max_length=10)


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: dict[str, Any]


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=200)
    business_category: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    city: str | None = None
    timezone: str = "Africa/Lagos"
    currency_code: str = Field(default="NGN", min_length=3, max_length=3)

    @field_validator("currency_code")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    status: Literal["active", "suspended", "archived"] | None = None
    business_category: str | None = None
    city: str | None = None
    timezone: str | None = None


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone_e164: str
    email: EmailStr | None = None
    role: Literal["owner", "admin", "member"] = "member"


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None


class PhoneCreate(BaseModel):
    user_id: str
    phone_e164: str
    label: str | None = Field(default=None, max_length=80)


class BumpaConnectionCreate(BaseModel):
    api_key: str = Field(min_length=4, max_length=500)
    scope_type: Literal["business_id", "location_id"]
    scope_id: str = Field(min_length=1, max_length=160)
    provider: Literal["local", "bumpa"] = "local"


class SyncRequest(BaseModel):
    date_from: date
    date_to: date

    @field_validator("date_to")
    @classmethod
    def validate_range(cls, value: date, info: Any) -> date:
        date_from = info.data.get("date_from")
        if date_from and value < date_from:
            raise ValueError("date_to must be on or after date_from")
        if date_from and (value - date_from).days > 366:
            raise ValueError("sync range cannot exceed 366 days")
        return value


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    client_message_id: str | None = Field(default=None, max_length=160)


class ChatResponse(BaseModel):
    conversation_id: str
    inbound_message_id: str
    outbound_message_id: str
    answer: str
    data_freshness: datetime | None = None


class ConsentUpdate(BaseModel):
    status: Literal["granted", "withdrawn"]
    policy_version: str = Field(default="v1", max_length=32)


class McpConnectionCreate(BaseModel):
    provider: Literal["google_drive", "google_sheets", "gmail", "calendar", "meta_ads"]
    scopes: list[str] = Field(default_factory=list, max_length=20)
    read_only: bool = True


class ReportCreate(BaseModel):
    report_type: Literal[
        "sme_usage", "cohort_behavior", "question_taxonomy", "weekly_memo", "monthly_memo"
    ] = "sme_usage"
    filters: dict[str, Any] = Field(default_factory=dict)
    formats: list[ReportFormat] = Field(default_factory=default_report_formats)


class ReportView(ORMModel):
    id: str
    report_type: str
    status: str
    title: str | None
    summary: str | None
    created_at: datetime
    finished_at: datetime | None


class ResearchConversationEventView(BaseModel):
    id: str
    user_pseudonym: str | None
    channel: str
    event_type: str
    redacted_text: str | None
    primary_intent: str | None
    business_function: str | None
    ai_help_type: str | None
    complexity: str | None
    bumpa_data_used: str | None
    created_at: datetime


class ResearchConversationSummaryView(BaseModel):
    id: str
    tenant_pseudonym: str | None
    participant_pseudonyms: list[str]
    channel: str
    event_count: int
    primary_intents: dict[str, int]
    latest_redacted_text: str | None
    started_at: datetime
    last_activity_at: datetime


class ResearchConversationDetailView(ResearchConversationSummaryView):
    events: list[ResearchConversationEventView]
