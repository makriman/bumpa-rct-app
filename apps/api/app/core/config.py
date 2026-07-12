from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Bumpa Bestie API"
    app_env: str = "local"
    api_prefix: str = "/v1"
    database_url: str = "sqlite:///./.data/bumpabestie.db"
    migration_database_url: str | None = None
    jwt_secret: str = "local-only-change-me-jwt-secret-32bytes"
    otp_secret: str = "local-only-change-me-otp-secret"
    field_encryption_key: str = "local-only-change-me-field-key"
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
    meta_system_user_access_token: str | None = None
    meta_system_user_access_token_file: Path | None = None
    meta_otp_template_name: str = "bb_otp_login"
    meta_template_language_code: str = "en"
    meta_request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    meta_max_response_bytes: int = Field(default=262_144, ge=4096, le=1_048_576)
    whatsapp_backend: Literal["mock", "disabled", "meta"] = "mock"
    bumpa_backend: Literal["mock", "disabled", "bumpa"] = "mock"
    agent_backend: Literal["mock", "disabled", "hermes"] = "mock"
    hermes_base_internal_host: str = "http://hermes"
    hermes_profile_root: Path = Path("./.data/hermes/profiles")
    hermes_profile_port_start: int = Field(default=8700, ge=1024, le=65535)
    hermes_profile_port_end: int = Field(default=8999, ge=1024, le=65535)
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
        if self.app_env == "production":
            insecure = (
                self.jwt_secret.startswith("local-only"),
                self.otp_secret.startswith("local-only"),
                self.field_encryption_key.startswith("local-only"),
            )
            if any(insecure):
                raise ValueError("Production secrets must be explicitly configured")
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
        return self


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_root.mkdir(parents=True, exist_ok=True)
    return settings
