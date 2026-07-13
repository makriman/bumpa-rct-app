from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Bumpa Bestie API"
    app_env: str = "local"
    api_prefix: str = "/v1"
    database_url: str = "sqlite:///./.data/bumpabestie.db"
    migration_database_url: str | None = None
    jwt_secret: str = "local-only-change-me-jwt-secret-32bytes"
    otp_secret: str = "local-only-change-me-otp-secret"
    field_encryption_key: str = "local-only-change-me-field-key"
    field_encryption_key_id: str = Field(
        default="primary", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    # The first dual-read release deliberately writes v1 so its v1-only
    # predecessor remains a valid rollback target. Production validation below
    # hard-locks this artifact to v1; local/test retain v2 coverage for the later
    # rollback-capability release that will own the v2 transition.
    field_encryption_write_version: Literal["v1", "v2"] = "v1"
    field_encryption_old_keys: dict[str, str] = Field(default_factory=dict)
    research_pseudonym_key: str = "local-only-change-me-research-pseudonym-key"
    onboarding_integrity_key: str = "local-only-change-me-onboarding-integrity-key"
    access_token_minutes: int = 60
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_request_cooldown_seconds: int = 30
    redis_url: str = "redis://redis:6379/0"
    auth_rate_limit_enabled: bool | None = None
    auth_rate_limit_window_seconds: int = Field(default=600, ge=60, le=3600)
    auth_request_phone_limit: int = Field(default=3, ge=1, le=100)
    auth_request_ip_limit: int = Field(default=60, ge=1, le=10_000)
    auth_verify_phone_limit: int = Field(default=10, ge=1, le=100)
    auth_verify_ip_limit: int = Field(default=120, ge=1, le=10_000)
    operation_rate_limit_enabled: bool | None = None
    chat_rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    chat_rate_limit: int = Field(default=12, ge=1, le=1000)
    whatsapp_rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    whatsapp_rate_limit: int = Field(default=12, ge=1, le=1000)
    bumpa_sync_rate_limit_window_seconds: int = Field(default=3600, ge=60, le=86_400)
    bumpa_sync_rate_limit: int = Field(default=6, ge=1, le=100)
    research_report_rate_limit_window_seconds: int = Field(default=3600, ge=60, le=86_400)
    research_report_rate_limit: int = Field(default=10, ge=1, le=100)
    local_otp_code: str = "246810"
    dev_fixed_otp: str | None = None
    dev_otp_sink: str = "log"
    expose_local_otp: bool = True
    seed_demo_data: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://admin.localhost:3000",
            "http://research.localhost:3000",
        ]
    )
    cors_allowed_origins: str | None = None
    artifact_root: Path = Path("./.data/exports")
    meta_graph_version: str = "v23.0"
    meta_app_id: str | None = None
    meta_waba_id: str | None = None
    meta_webhook_verify_token: str | None = None
    meta_webhook_verify_token_file: Path | None = None
    meta_app_secret: str | None = None
    meta_app_secret_file: Path | None = None
    meta_phone_number_id: str | None = None
    meta_test_sender_verification_mode: Literal["disabled", "inbound_replies_only"] = "disabled"
    meta_test_sender_waba_id: str | None = Field(default=None, pattern=r"^\d{5,64}$")
    meta_test_sender_phone_number_id: str | None = Field(default=None, pattern=r"^\d{5,64}$")
    meta_test_sender_display_phone_e164: str | None = Field(
        default=None, pattern=r"^\+[1-9]\d{7,14}$"
    )
    meta_system_user_access_token: str | None = None
    meta_system_user_access_token_file: Path | None = None
    meta_otp_template_name: str = "bb_otp_login"
    meta_daily_insight_template_name: str = "bb_daily_insight"
    meta_weekly_insight_template_name: str = "bb_weekly_insight"
    meta_template_language_code: str = "en"
    meta_request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    meta_max_response_bytes: int = Field(default=262_144, ge=4096, le=1_048_576)
    proactive_insights_enabled: bool = False
    daily_insights_enabled: bool = False
    weekly_insights_enabled: bool = False
    daily_insight_local_hour: int = Field(default=8, ge=0, le=23)
    weekly_insight_local_weekday: int = Field(default=0, ge=0, le=6)
    weekly_insight_local_hour: int = Field(default=8, ge=0, le=23)
    insight_max_freshness_hours: int = Field(default=48, ge=1, le=744)
    ops_alerts_enabled: bool = False
    ops_alert_webhook_url: str | None = None
    ops_alert_hmac_secret: str | None = None
    ops_alert_hmac_secret_file: Path | None = None
    ops_alert_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    ops_alert_max_attempts: int = Field(default=3, ge=1, le=5)
    ops_alert_max_response_bytes: int = Field(default=65_536, ge=1024, le=262_144)
    ops_alert_scan_lookback_hours: int = Field(default=168, ge=1, le=2160)
    ops_alert_scan_limit: int = Field(default=100, ge=1, le=500)
    hermes_health_alert_interval_minutes: int = Field(default=15, ge=5, le=1440)
    audit_log_retention_days: int = Field(default=365, ge=30, le=3650)
    system_error_retention_days: int = Field(default=90, ge=7, le=3650)
    operational_retention_batch_size: int = Field(default=500, ge=1, le=1000)
    public_origin: str = "http://bumpabestie.localhost:8080"
    api_origin: str = "http://api.bumpabestie.localhost:8080"
    mcp_google_oauth_enabled: bool = False
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_client_secret_file: Path | None = None
    mcp_meta_ads_oauth_enabled: bool = False
    meta_ads_oauth_client_id: str | None = None
    meta_ads_oauth_client_secret: str | None = None
    meta_ads_oauth_client_secret_file: Path | None = None
    mcp_oauth_state_ttl_seconds: int = Field(default=600, ge=120, le=1800)
    mcp_oauth_request_timeout_seconds: float = Field(default=15.0, ge=1.0, le=30.0)
    mcp_oauth_max_response_bytes: int = Field(default=131_072, ge=4096, le=524_288)
    whatsapp_backend: Literal["mock", "disabled", "meta"] = "mock"
    bumpa_backend: Literal["mock", "disabled", "bumpa"] = "mock"
    agent_backend: Literal["mock", "disabled", "hermes"] = "mock"
    hermes_base_internal_host: str = "http://hermes"
    hermes_profile_root: Path = Path("./.data/hermes/profiles")
    hermes_profile_port_start: int = Field(default=8700, ge=1024, le=65535)
    hermes_profile_port_end: int = Field(default=8999, ge=1024, le=65535)
    hermes_control_port: int = Field(default=8699, ge=1024, le=65535)
    hermes_default_model: str = Field(
        default="claude-sonnet-4-6",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$",
    )
    hermes_connect_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30.0)
    hermes_read_timeout_seconds: float = Field(default=90.0, ge=1.0, le=180.0)
    hermes_max_response_bytes: int = Field(default=262_144, ge=4096, le=1_048_576)
    hermes_max_context_chars: int = Field(default=12_000, ge=1000, le=100_000)
    hermes_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    hermes_circuit_recovery_seconds: float = Field(default=30.0, ge=1.0, le=600.0)
    session_cookie_name: str = "bb_session"
    session_cookie_domain: str | None = None
    session_cookie_secure: bool = False

    @field_validator(
        "meta_webhook_verify_token_file",
        "meta_app_secret_file",
        "meta_system_user_access_token_file",
        "ops_alert_hmac_secret_file",
        "google_oauth_client_secret_file",
        "meta_ads_oauth_client_secret_file",
        "meta_test_sender_waba_id",
        "meta_test_sender_phone_number_id",
        "meta_test_sender_display_phone_e164",
        mode="before",
    )
    @classmethod
    def blank_optional_environment_values(cls, value: object) -> object:
        """Treat Compose's empty optional variables as unset before coercion."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("field_encryption_old_keys", mode="before")
    @classmethod
    def blank_field_encryption_old_keys(cls, value: object) -> object:
        """Let Compose omit the optional JSON key ring without changing local defaults."""

        if value == "":
            return {}
        return value

    @property
    def is_local(self) -> bool:
        return self.app_env in {"local", "test"}

    @property
    def effective_local_otp_code(self) -> str:
        return self.dev_fixed_otp or self.local_otp_code

    @property
    def effective_cors_origins(self) -> list[str]:
        if not self.cors_allowed_origins:
            return self.cors_origins
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def cookie_secure(self) -> bool:
        return self.app_env == "production" or self.session_cookie_secure

    @property
    def cookie_domain(self) -> str | None:
        return self.session_cookie_domain.strip() if self.session_cookie_domain else None

    @property
    def effective_auth_rate_limit_enabled(self) -> bool:
        if self.auth_rate_limit_enabled is not None:
            return self.auth_rate_limit_enabled
        return self.app_env == "production"

    @property
    def effective_operation_rate_limit_enabled(self) -> bool:
        if self.operation_rate_limit_enabled is not None:
            return self.operation_rate_limit_enabled
        return self.app_env == "production"

    @property
    def allowed_meta_inbound_reply_senders(self) -> frozenset[tuple[str, str]]:
        """Return configured ``(WABA ID, phone-number ID)`` reply sender pairs."""

        senders: set[tuple[str, str]] = set()
        if self.meta_waba_id and self.meta_phone_number_id:
            senders.add((self.meta_waba_id, self.meta_phone_number_id))
        if (
            self.meta_test_sender_verification_mode == "inbound_replies_only"
            and self.meta_test_sender_waba_id
            and self.meta_test_sender_phone_number_id
        ):
            senders.add((self.meta_test_sender_waba_id, self.meta_test_sender_phone_number_id))
        return frozenset(senders)

    @property
    def effective_meta_webhook_verify_token(self) -> str:
        return self._provider_secret(
            "META_WEBHOOK_VERIFY_TOKEN",
            self.meta_webhook_verify_token,
            self.meta_webhook_verify_token_file,
            local_default="local-webhook-token",
        )

    @property
    def effective_meta_app_secret(self) -> str:
        return self._provider_secret(
            "META_APP_SECRET",
            self.meta_app_secret,
            self.meta_app_secret_file,
            local_default="local-meta-app-secret",
        )

    @property
    def effective_meta_system_user_access_token(self) -> str:
        return self._provider_secret(
            "META_SYSTEM_USER_ACCESS_TOKEN",
            self.meta_system_user_access_token,
            self.meta_system_user_access_token_file,
        )

    @property
    def effective_ops_alert_hmac_secret(self) -> str:
        return self._provider_secret(
            "OPS_ALERT_HMAC_SECRET",
            self.ops_alert_hmac_secret,
            self.ops_alert_hmac_secret_file,
        )

    @property
    def effective_google_oauth_client_secret(self) -> str:
        return self._provider_secret(
            "GOOGLE_OAUTH_CLIENT_SECRET",
            self.google_oauth_client_secret,
            self.google_oauth_client_secret_file,
        )

    @property
    def effective_meta_ads_oauth_client_secret(self) -> str:
        return self._provider_secret(
            "META_ADS_OAUTH_CLIENT_SECRET",
            self.meta_ads_oauth_client_secret,
            self.meta_ads_oauth_client_secret_file,
        )

    @property
    def mcp_oauth_callback_url(self) -> str:
        # OAuth is initiated by the browser through the same-origin Next.js BFF.
        # Return through that boundary as well so the host-only HttpOnly session
        # cookie remains available without widening it to every application
        # subdomain. The BFF forwards this fixed allowlisted path to the API.
        return f"{self.public_origin.rstrip('/')}/api/backend/settings/mcp-oauth/callback"

    def _provider_secret(
        self,
        name: str,
        inline_value: str | None,
        file_path: Path | None,
        *,
        local_default: str | None = None,
    ) -> str:
        if inline_value and file_path:
            raise ValueError(f"Set either {name} or {name}_FILE, not both")
        if file_path:
            if not file_path.is_absolute() and not self.is_local:
                raise ValueError(f"{name}_FILE must be an absolute path outside local mode")
            try:
                value = file_path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise ValueError(f"Unable to read {name}_FILE") from exc
            if "\n" in value or "\r" in value:
                raise ValueError(f"{name}_FILE must contain exactly one line")
            if value:
                return value
            raise ValueError(f"{name}_FILE is empty")
        if inline_value:
            return inline_value
        if self.is_local and local_default:
            return local_default
        return ""

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> Settings:
        self._validate_meta_test_sender_verification()
        if self.app_env != "production":
            self._validate_field_encryption_keys()
        if self.system_error_retention_days * 24 < self.ops_alert_scan_lookback_hours:
            raise ValueError("SYSTEM_ERROR_RETENTION_DAYS must cover OPS_ALERT_SCAN_LOOKBACK_HOURS")
        if self.app_env == "production":
            insecure = (
                self.jwt_secret.startswith("local-only"),
                self.otp_secret.startswith("local-only"),
                self.field_encryption_key.startswith("local-only"),
                self.research_pseudonym_key.startswith("local-only"),
                self.onboarding_integrity_key.startswith("local-only"),
            )
            if any(insecure):
                raise ValueError("Production secrets must be explicitly configured")
            if len(self.research_pseudonym_key) < 24 or len(self.onboarding_integrity_key) < 24:
                raise ValueError("Application integrity keys must contain at least 24 characters")
            self._validate_field_encryption_keys()
            if self.field_encryption_write_version != "v1":
                raise ValueError(
                    "Production FIELD_ENCRYPTION_WRITE_VERSION must remain v1 "
                    "during the first dual-reader soak"
                )
            if not self.effective_auth_rate_limit_enabled:
                raise ValueError("Production authentication rate limiting cannot be disabled")
            if not self.effective_operation_rate_limit_enabled:
                raise ValueError("Production operation rate limiting cannot be disabled")
            if self.expose_local_otp or self.seed_demo_data or self.dev_fixed_otp is not None:
                raise ValueError(
                    "Local OTP controls and demo seeding must be disabled in production"
                )
            mocked = [
                name
                for name, value in {
                    "WHATSAPP_BACKEND": self.whatsapp_backend,
                    "BUMPA_BACKEND": self.bumpa_backend,
                    "AGENT_BACKEND": self.agent_backend,
                }.items()
                if value == "mock"
            ]
            if mocked:
                raise ValueError("Production cannot use mock providers: " + ", ".join(mocked))
            if self.agent_backend == "hermes":
                parsed_hermes_host = urlsplit(self.hermes_base_internal_host)
                if not (
                    parsed_hermes_host.scheme == "http"
                    and parsed_hermes_host.hostname
                    and parsed_hermes_host.hostname != "localhost"
                    and re.fullmatch(
                        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                        parsed_hermes_host.hostname,
                    )
                    and parsed_hermes_host.port is None
                    and parsed_hermes_host.path in {"", "/"}
                    and not parsed_hermes_host.query
                    and not parsed_hermes_host.fragment
                    and parsed_hermes_host.username is None
                    and parsed_hermes_host.password is None
                ):
                    raise ValueError(
                        "HERMES_BASE_INTERNAL_HOST must be an uncredentialed private HTTP host"
                    )
                if not self.hermes_profile_root.is_absolute():
                    raise ValueError("HERMES_PROFILE_ROOT must be absolute in production")
                if self.hermes_profile_port_start > self.hermes_profile_port_end:
                    raise ValueError("Hermes profile port range is invalid")
                if (
                    self.hermes_profile_port_start
                    <= self.hermes_control_port
                    <= self.hermes_profile_port_end
                ):
                    raise ValueError("Hermes control port must be outside the profile port range")
                if not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", self.hermes_default_model
                ):
                    raise ValueError("HERMES_DEFAULT_MODEL is invalid")
            if self.whatsapp_backend == "meta":
                if not re.fullmatch(r"v\d+\.\d+", self.meta_graph_version):
                    raise ValueError("META_GRAPH_VERSION must use the form v<major>.<minor>")
                if not re.fullmatch(r"[a-z0-9_]{1,512}", self.meta_otp_template_name):
                    raise ValueError("META_OTP_TEMPLATE_NAME is invalid")
                if not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", self.meta_template_language_code):
                    raise ValueError("META_TEMPLATE_LANGUAGE_CODE is invalid")
                missing = [
                    name
                    for name, value in {
                        "META_APP_ID": self.meta_app_id,
                        "META_WABA_ID": self.meta_waba_id,
                        "META_WEBHOOK_VERIFY_TOKEN": self.effective_meta_webhook_verify_token,
                        "META_APP_SECRET": self.effective_meta_app_secret,
                        "META_PHONE_NUMBER_ID": self.meta_phone_number_id,
                        "META_SYSTEM_USER_ACCESS_TOKEN": (
                            self.effective_meta_system_user_access_token
                        ),
                    }.items()
                    if not value or str(value).startswith("local-")
                ]
                if missing:
                    raise ValueError(
                        "Meta WhatsApp configuration is incomplete: " + ", ".join(missing)
                    )
                identifier_values = {
                    "META_APP_ID": self.meta_app_id,
                    "META_WABA_ID": self.meta_waba_id,
                    "META_PHONE_NUMBER_ID": self.meta_phone_number_id,
                }
                invalid_ids = [
                    name
                    for name, value in identifier_values.items()
                    if not value or not re.fullmatch(r"\d{5,64}", value)
                ]
                if invalid_ids:
                    raise ValueError("Meta identifiers must be numeric: " + ", ".join(invalid_ids))
                short_secrets = [
                    name
                    for name, value, minimum in (
                        (
                            "META_WEBHOOK_VERIFY_TOKEN",
                            self.effective_meta_webhook_verify_token,
                            24,
                        ),
                        ("META_APP_SECRET", self.effective_meta_app_secret, 24),
                        (
                            "META_SYSTEM_USER_ACCESS_TOKEN",
                            self.effective_meta_system_user_access_token,
                            32,
                        ),
                    )
                    if len(value) < minimum
                ]
                if short_secrets:
                    raise ValueError(
                        "Meta secrets do not meet minimum length: " + ", ".join(short_secrets)
                    )
            self._validate_proactive_insights()
            self._validate_ops_alerts()
            self._validate_mcp_oauth_configuration()
        return self

    def _validate_field_encryption_keys(self) -> None:
        if len(self.field_encryption_old_keys) > 16:
            raise ValueError("At most 16 old field-encryption keys may be configured")
        if self.field_encryption_key_id in self.field_encryption_old_keys:
            raise ValueError("Current field-encryption key ID must not appear in the old-key ring")
        invalid_ids = [
            key_id
            for key_id in self.field_encryption_old_keys
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key_id)
        ]
        if invalid_ids:
            raise ValueError("Old field-encryption key IDs are invalid")
        if any(not secret for secret in self.field_encryption_old_keys.values()):
            raise ValueError("Old field-encryption keys must not be empty")
        if self.app_env != "production":
            return
        configured_keys = {
            self.field_encryption_key_id: self.field_encryption_key,
            **self.field_encryption_old_keys,
        }
        if any(
            len(secret) < 24
            or secret.startswith("local-only")
            or "ADD_VALUE" in secret
            or "change-me" in secret
            for secret in configured_keys.values()
        ):
            raise ValueError("Field-encryption keys are too short or use placeholders")

    def _validate_meta_test_sender_verification(self) -> None:
        if self.meta_test_sender_verification_mode == "disabled":
            return
        if self.whatsapp_backend != "meta":
            raise ValueError("Meta test-sender verification requires the Meta WhatsApp backend")
        required = {
            "META_WABA_ID": self.meta_waba_id,
            "META_PHONE_NUMBER_ID": self.meta_phone_number_id,
            "META_TEST_SENDER_WABA_ID": self.meta_test_sender_waba_id,
            "META_TEST_SENDER_PHONE_NUMBER_ID": self.meta_test_sender_phone_number_id,
            "META_TEST_SENDER_DISPLAY_PHONE_E164": self.meta_test_sender_display_phone_e164,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("Meta test-sender verification is incomplete: " + ", ".join(missing))
        if self.meta_test_sender_phone_number_id == self.meta_phone_number_id:
            raise ValueError(
                "META_TEST_SENDER_PHONE_NUMBER_ID must differ from META_PHONE_NUMBER_ID"
            )

    def _validate_proactive_insights(self) -> None:
        if not self.proactive_insights_enabled:
            if self.daily_insights_enabled or self.weekly_insights_enabled:
                raise ValueError(
                    "Daily or weekly insights cannot be enabled while proactive insights are disabled"
                )
            return
        if self.whatsapp_backend != "meta":
            raise ValueError("Proactive insights require the Meta WhatsApp backend")
        if not (self.daily_insights_enabled or self.weekly_insights_enabled):
            raise ValueError("Proactive insights require at least one cadence")
        for name, value in (
            ("META_DAILY_INSIGHT_TEMPLATE_NAME", self.meta_daily_insight_template_name),
            ("META_WEEKLY_INSIGHT_TEMPLATE_NAME", self.meta_weekly_insight_template_name),
        ):
            if not re.fullmatch(r"[a-z0-9_]{1,512}", value):
                raise ValueError(f"{name} is invalid")

    def _validate_ops_alerts(self) -> None:
        if not self.ops_alerts_enabled:
            return
        if self.ops_alert_hmac_secret:
            raise ValueError("OPS_ALERT_HMAC_SECRET must use a secret file in production")
        if self.ops_alert_hmac_secret_file is None:
            raise ValueError("OPS_ALERT_HMAC_SECRET_FILE is required when alerts are enabled")
        if len(self.effective_ops_alert_hmac_secret) < 32:
            raise ValueError("OPS_ALERT_HMAC_SECRET_FILE is too short")
        parsed = urlsplit(self.ops_alert_webhook_url or "")
        if not (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path not in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        ):
            raise ValueError(
                "OPS_ALERT_WEBHOOK_URL must be an uncredentialed HTTPS URL without query or fragment"
            )

    def _validate_mcp_oauth_configuration(self) -> None:
        if not (self.mcp_google_oauth_enabled or self.mcp_meta_ads_oauth_enabled):
            return
        for name, value in (("PUBLIC_ORIGIN", self.public_origin), ("API_ORIGIN", self.api_origin)):
            parsed = urlsplit(value)
            if not (
                parsed.scheme == "https"
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            ):
                raise ValueError(f"{name} must be an uncredentialed HTTPS origin in production")
        if self.mcp_google_oauth_enabled:
            if not self.google_oauth_client_id or not self.effective_google_oauth_client_secret:
                raise ValueError("Google MCP OAuth is enabled without client credentials")
            if self.google_oauth_client_secret_file is None:
                raise ValueError("Google MCP OAuth client secret must use a file in production")
            if not 5 <= len(self.google_oauth_client_id) <= 512 or any(
                character.isspace() for character in self.google_oauth_client_id
            ):
                raise ValueError("Google MCP OAuth client ID is invalid")
            if len(self.effective_google_oauth_client_secret) < 24:
                raise ValueError("Google MCP OAuth client secret is too short")
        if self.mcp_meta_ads_oauth_enabled:
            if not self.meta_ads_oauth_client_id or not self.effective_meta_ads_oauth_client_secret:
                raise ValueError("Meta Ads MCP OAuth is enabled without client credentials")
            if self.meta_ads_oauth_client_secret_file is None:
                raise ValueError("Meta Ads MCP OAuth client secret must use a file in production")
            if not re.fullmatch(r"v\d+\.\d+", self.meta_graph_version):
                raise ValueError("META_GRAPH_VERSION must use the form v<major>.<minor>")
            if not re.fullmatch(r"\d{5,64}", self.meta_ads_oauth_client_id):
                raise ValueError("Meta Ads MCP OAuth client ID must be numeric")
            if len(self.effective_meta_ads_oauth_client_secret) < 24:
                raise ValueError("Meta Ads MCP OAuth client secret is too short")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_root.mkdir(parents=True, exist_ok=True)
    return settings
