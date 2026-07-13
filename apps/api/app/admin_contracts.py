from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class TenantPersonView(BaseModel):
    membership_id: str
    user_id: str
    name: str | None
    phone_masked: str
    role: str
    status: str


class TenantPhoneView(BaseModel):
    id: str
    user_id: str
    phone_masked: str
    label: str | None
    status: str
    opt_out: bool


class BumpaConnectionStatusView(BaseModel):
    connected: bool
    status: str
    scope_type: str | None
    scope_id_last4: str | None
    provider: str | None
    last_successful_sync_at: datetime | None
    last_failed_sync_at: datetime | None
    last_error: str | None


class HermesProfileStatusView(BaseModel):
    provisioned: bool
    profile_name: str | None
    provider: str | None
    status: str
    api_port: int | None


class TenantOperationsView(BaseModel):
    tenant_id: str
    people: list[TenantPersonView]
    phones: list[TenantPhoneView]
    bumpa: BumpaConnectionStatusView
    hermes: HermesProfileStatusView


class AdminBumpaSyncRequest(BaseModel):
    date_from: date
    date_to: date
    reason: Literal[
        "operator_requested_refresh",
        "connection_verified",
        "provider_recovered",
        "data_freshness_recovery",
    ]
    confirmation: Literal["trigger_bumpa_sync"]

    @model_validator(mode="after")
    def validate_range(self) -> AdminBumpaSyncRequest:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        if (self.date_to - self.date_from).days > 366:
            raise ValueError("Sync range cannot exceed 367 days")
        return self


class AdminSyncJobView(BaseModel):
    job_id: str
    status: str
    duplicate: bool
    requested_from: date
    requested_to: date


class HermesRestartRequest(BaseModel):
    reason: Literal[
        "profile_unresponsive",
        "configuration_refreshed",
        "provider_recovered",
        "operator_health_recovery",
    ]
    confirmation: Literal["restart_hermes_profile"]


class HermesRestartView(BaseModel):
    profile_name: str
    status: str
    control_status: Literal["restarted"]


class WhatsappDeliveryFailureView(BaseModel):
    id: str
    tenant_id: str | None
    message_reference: str
    phone_masked: str | None
    status: str
    provider_error_code: str | None
    provider_error_title: str | None
    created_at: datetime


class HermesCallErrorView(BaseModel):
    id: str
    tenant_id: str | None
    category: str
    retryable: bool | None
    profile_reference: str | None
    created_at: datetime


class AdminExportRequest(BaseModel):
    format: Literal["csv"] = "csv"
    scope: Literal["tenant_operations"] = "tenant_operations"
    confirmation: Literal["generate_admin_export"]


class AdminExportView(BaseModel):
    export_id: str
    filename: str
    content_type: Literal["text/csv"]
    content: str
    row_count: int
    checksum_sha256: str
