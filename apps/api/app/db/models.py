from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, IdMixin, TimestampMixin

JsonDict = dict[str, Any]
IpAddressType = String(45).with_variant(postgresql.INET(), "postgresql")


class Tenant(IdMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    business_category: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Lagos")
    currency_code: Mapped[str] = mapped_column(String(3), default="NGN")
    research_consent_status: Mapped[str] = mapped_column(String(24), default="pending")


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    primary_phone_e164: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="active")


class PlatformRole(IdMixin, TimestampMixin, Base):
    __tablename__ = "platform_roles"
    __table_args__ = (UniqueConstraint("user_id", "role"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(24), index=True)


class TenantMembership(IdMixin, TimestampMixin, Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'researcher', 'operator', 'superadmin')",
            name="ck_tenant_memberships_role",
        ),
        UniqueConstraint("tenant_id", "user_id"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(24), default="member")
    status: Mapped[str] = mapped_column(String(24), default="active")


class PhoneIdentity(IdMixin, TimestampMixin, Base):
    __tablename__ = "phone_identities"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    whatsapp_wa_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    label: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="approved")
    opt_out: Mapped[bool] = mapped_column(Boolean, default=False)


class OtpSession(IdMixin, Base):
    __tablename__ = "otp_sessions"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('login', 'invite', 'phone_verify')",
            name="ck_otp_sessions_purpose",
        ),
        CheckConstraint("attempts >= 0", name="ck_otp_sessions_attempts_nonnegative"),
        Index("ix_otp_sessions_phone_expires", "phone_e164", "expires_at"),
    )

    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(24), default="login")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_jti_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchConsent(IdMixin, Base):
    __tablename__ = "research_consent_history"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    policy_version: Mapped[str] = mapped_column(String(32), default="v1")
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BumpaConnection(IdMixin, TimestampMixin, Base):
    __tablename__ = "bumpa_connections"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('business_id', 'location_id')",
            name="ck_bumpa_connections_scope_type",
        ),
        UniqueConstraint("tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(24), default="local")
    status: Mapped[str] = mapped_column(String(24), default="active")
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class BumpaSyncRun(IdMixin, Base):
    __tablename__ = "bumpa_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'partial', 'failed')",
            name="ck_bumpa_sync_runs_status",
        ),
        CheckConstraint(
            "rate_limit_limit IS NULL OR rate_limit_limit >= 0",
            name="ck_bumpa_sync_runs_rate_limit_nonnegative",
        ),
        CheckConstraint(
            "rate_limit_remaining IS NULL OR rate_limit_remaining >= 0",
            name="ck_bumpa_sync_runs_rate_remaining_nonnegative",
        ),
        CheckConstraint(
            "rate_limit_limit IS NULL OR rate_limit_remaining IS NULL "
            "OR rate_limit_remaining <= rate_limit_limit",
            name="ck_bumpa_sync_runs_rate_remaining_within_limit",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    bumpa_connection_id: Mapped[str] = mapped_column(
        ForeignKey("bumpa_connections.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    requested_from: Mapped[date] = mapped_column(Date)
    requested_to: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    rate_limit_limit: Mapped[int | None] = mapped_column(Integer)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    dataset_results: Mapped[JsonDict] = mapped_column(JSON, default=dict)


class BumpaRawResponse(IdMixin, Base):
    __tablename__ = "bumpa_raw_responses"
    __table_args__ = (
        CheckConstraint(
            "availability IN ('available', 'unavailable', 'error')",
            name="ck_bumpa_raw_responses_availability",
        ),
        CheckConstraint(
            "http_status BETWEEN 100 AND 599",
            name="ck_bumpa_raw_responses_http_status",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    sync_run_id: Mapped[str] = mapped_column(ForeignKey("bumpa_sync_runs.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(String(80))
    dataset: Mapped[str | None] = mapped_column(String(80))
    http_status: Mapped[int] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(24))
    error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[JsonDict] = mapped_column(JSON)
    pii_level: Mapped[str] = mapped_column(String(24), default="sensitive")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BumpaMetricSnapshot(IdMixin, Base):
    __tablename__ = "bumpa_metric_snapshots"
    __table_args__ = (
        Index("ix_metric_tenant_key_created", "tenant_id", "metric_key", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    sync_run_id: Mapped[str] = mapped_column(ForeignKey("bumpa_sync_runs.id", ondelete="CASCADE"))
    metric_key: Mapped[str] = mapped_column(String(120))
    metric_title: Mapped[str | None] = mapped_column(String(200))
    value_decimal: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    value_text: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    requested_from: Mapped[date] = mapped_column(Date)
    requested_to: Mapped[date] = mapped_column(Date)
    response_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability: Mapped[str] = mapped_column(String(24), default="available")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BumpaOrder(IdMixin, TimestampMixin, Base):
    __tablename__ = "bumpa_orders"
    __table_args__ = (
        CheckConstraint(
            "currency_code IS NULL OR length(currency_code) = 3",
            name="ck_bumpa_orders_currency_code_length",
        ),
        UniqueConstraint("tenant_id", "bumpa_order_id"),
        Index("ix_bumpa_orders_tenant_order_date", "tenant_id", "order_date"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    bumpa_order_id: Mapped[str] = mapped_column(String(120))
    order_number: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(80))
    payment_status: Mapped[str | None] = mapped_column(String(80))
    shipping_status: Mapped[str | None] = mapped_column(String(80))
    channel: Mapped[str | None] = mapped_column(String(80))
    origin: Mapped[str | None] = mapped_column(String(120))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    subtotal_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    shipping_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    amount_due: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    order_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[JsonDict] = mapped_column(JSON)


class BumpaOrderItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "bumpa_order_items"
    __table_args__ = (
        CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_bumpa_order_items_quantity_nonnegative",
        ),
        Index("ix_bumpa_order_items_tenant_order", "tenant_id", "order_id"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    order_id: Mapped[str] = mapped_column(ForeignKey("bumpa_orders.id", ondelete="CASCADE"))
    bumpa_item_id: Mapped[str | None] = mapped_column(String(120))
    product_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(300))
    unit: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    raw_payload: Mapped[JsonDict] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    correlation_id: Mapped[str | None] = mapped_column(String(80))


class HermesProfile(IdMixin, TimestampMixin, Base):
    __tablename__ = "hermes_profiles"
    __table_args__ = (
        CheckConstraint(
            "api_port IS NULL OR api_port BETWEEN 1024 AND 65535",
            name="ck_hermes_profiles_api_port_range",
        ),
        CheckConstraint(
            "provider != 'hermes' OR (profile_path IS NOT NULL AND api_port IS NOT NULL)",
            name="ck_hermes_profiles_live_coordinates",
        ),
        UniqueConstraint("tenant_id"),
        UniqueConstraint("profile_name"),
        UniqueConstraint("api_port", name="uq_hermes_profiles_api_port"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    profile_name: Mapped[str] = mapped_column(String(160))
    profile_path: Mapped[str | None] = mapped_column(String(500))
    provider: Mapped[str] = mapped_column(String(24), default="local")
    api_internal_url: Mapped[str] = mapped_column(String(300), default="local://agent")
    api_port: Mapped[int | None] = mapped_column(Integer)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active")


class Conversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversation_tenant_updated", "tenant_id", "updated_at"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="open")
    title: Mapped[str | None] = mapped_column(String(200))


class AgentMessage(IdMixin, Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('web', 'whatsapp', 'system', 'admin')",
            name="ck_agent_messages_channel",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_agent_messages_direction",
        ),
        UniqueConstraint("channel", "external_message_id"),
        Index(
            "ix_message_tenant_conversation_created", "tenant_id", "conversation_id", "created_at"
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    hermes_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("hermes_profiles.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(24))
    direction: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    redacted_content: Mapped[str | None] = mapped_column(Text)
    external_message_id: Mapped[str | None] = mapped_column(String(160))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentToolCall(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'failed', 'denied')",
            name="ck_agent_tool_calls_status",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_tool_calls_duration_nonnegative",
        ),
        Index(
            "ix_agent_tool_calls_tenant_message_created",
            "tenant_id",
            "agent_message_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    agent_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(160))
    tool_input: Mapped[JsonDict | None] = mapped_column(JSON)
    tool_output: Mapped[JsonDict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    correlation_id: Mapped[str | None] = mapped_column(String(80))


class WhatsappMessage(IdMixin, Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_whatsapp_messages_direction",
        ),
    )

    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)
    meta_message_id: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)
    wa_id: Mapped[str | None] = mapped_column(String(40))
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(24))
    message_type: Mapped[str | None] = mapped_column(String(40))
    text_body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[JsonDict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WhatsappDeliveryEvent(IdMixin, Base):
    __tablename__ = "whatsapp_delivery_events"
    __table_args__ = (UniqueConstraint("meta_message_id", "status", "event_timestamp"),)

    whatsapp_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="CASCADE")
    )
    meta_message_id: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40))
    event_timestamp: Mapped[str] = mapped_column(String(40), default="unknown")
    payload: Mapped[JsonDict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(IdMixin, Base):
    __tablename__ = "provider_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "external_event_id"),)

    provider: Mapped[str] = mapped_column(String(24))
    external_event_id: Mapped[str] = mapped_column(String(160))
    signature_valid: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[JsonDict] = mapped_column(JSON)
    processing_status: Mapped[str] = mapped_column(String(24), default="received")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchEvent(IdMixin, Base):
    __tablename__ = "research_events"
    __table_args__ = (
        Index("ix_research_filters", "tenant_id", "channel", "primary_intent", "created_at"),
    )

    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    agent_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(24))
    event_type: Mapped[str] = mapped_column(String(80))
    redacted_text: Mapped[str | None] = mapped_column(Text)
    primary_intent: Mapped[str | None] = mapped_column(String(80), index=True)
    business_function: Mapped[str | None] = mapped_column(String(80))
    ai_help_type: Mapped[str | None] = mapped_column(String(80))
    complexity: Mapped[str | None] = mapped_column(String(80))
    bumpa_data_used: Mapped[str | None] = mapped_column(String(80))
    classification_version: Mapped[str | None] = mapped_column(String(40))
    outcome: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    pii_redacted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchReport(IdMixin, Base):
    __tablename__ = "research_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'failed')",
            name="ck_research_reports_status",
        ),
    )

    report_type: Mapped[str] = mapped_column(String(80))
    generated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filters: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(IdMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("report_id", "format"),)

    report_id: Mapped[str] = mapped_column(ForeignKey("research_reports.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(String(12))
    storage_key: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class McpConnection(IdMixin, TimestampMixin, Base):
    __tablename__ = "mcp_connections"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('google_drive', 'google_sheets', 'gmail', 'calendar', 'meta_ads')",
            name="ck_mcp_connections_provider",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="disabled")
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_approved: Mapped[bool] = mapped_column(Boolean, default=False)


class McpToolPermission(IdMixin, TimestampMixin, Base):
    __tablename__ = "mcp_tool_permissions"
    __table_args__ = (
        CheckConstraint(
            "permission IN ('deny', 'read', 'write_with_confirmation')",
            name="ck_mcp_tool_permissions_permission",
        ),
        UniqueConstraint(
            "mcp_connection_id",
            "tool_name",
            name="uq_mcp_tool_permissions_connection_tool",
        ),
        Index(
            "ix_mcp_tool_permissions_tenant_connection",
            "tenant_id",
            "mcp_connection_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    mcp_connection_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE")
    )
    tool_name: Mapped[str] = mapped_column(String(160))
    permission: Mapped[str] = mapped_column(String(32), default="deny")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    correlation_id: Mapped[str | None] = mapped_column(String(80))


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(160), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    ip_address: Mapped[str | None] = mapped_column(IpAddressType)
    user_agent: Mapped[str | None] = mapped_column(Text)
    before: Mapped[JsonDict | None] = mapped_column(JSON)
    after: Mapped[JsonDict | None] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemError(IdMixin, Base):
    __tablename__ = "system_errors"
    __table_args__ = (
        Index("ix_system_errors_service_severity_created", "service", "severity", "created_at"),
    )

    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    service: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(Text)
    stack: Mapped[str | None] = mapped_column(Text)
    error_metadata: Mapped[JsonDict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AsyncJob(IdMixin, TimestampMixin, Base):
    """Durable unit of asynchronous work.

    Redis is only a wake-up transport. The job record, retry state and terminal
    outcome live in PostgreSQL so a Redis restart cannot lose accepted work.
    """

    __tablename__ = "async_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'retry', 'succeeded', "
            "'dead_letter', 'cancelled')",
            name="ck_async_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_async_jobs_attempts_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_async_jobs_max_attempts_positive"),
        UniqueConstraint("queue_name", "idempotency_key", name="uq_async_jobs_idempotency"),
        Index("ix_async_jobs_dispatch", "status", "available_at", "created_at"),
        Index("ix_async_jobs_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )
    queue_name: Mapped[str] = mapped_column(String(80), default="default")
    kind: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(160))
    last_error: Mapped[str | None] = mapped_column(String(240))
    result: Mapped[JsonDict | None] = mapped_column(JSON)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobOutbox(IdMixin, TimestampMixin, Base):
    """Transactional handoff from PostgreSQL to the Redis wake-up queue."""

    __tablename__ = "job_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'dispatched')", name="ck_job_outbox_status"),
        CheckConstraint("dispatch_attempts >= 0", name="ck_job_outbox_attempts_nonnegative"),
        UniqueConstraint("job_id", name="uq_job_outbox_job"),
        Index("ix_job_outbox_due", "status", "available_at", "created_at"),
    )

    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("async_jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(240))


class UsageEvent(IdMixin, Base):
    __tablename__ = "usage_events"

    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event_name: Mapped[str] = mapped_column(String(100))
    units: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    event_metadata: Mapped[JsonDict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
