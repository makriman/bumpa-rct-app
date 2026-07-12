from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    meta_webhook_verify_token: str = "local-webhook-token"
    meta_app_secret: str = "local-meta-app-secret"
    meta_phone_number_id: str | None = None
    meta_system_user_access_token: str | None = None
    whatsapp_backend: Literal["mock", "disabled", "meta"] = "mock"
    bumpa_backend: Literal["mock", "disabled", "bumpa"] = "mock"
    agent_backend: Literal["mock", "disabled", "hermes"] = "mock"
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
            if self.whatsapp_backend == "meta":
                missing = [
                    name
                    for name, value in {
                        "META_APP_SECRET": self.meta_app_secret,
                        "META_PHONE_NUMBER_ID": self.meta_phone_number_id,
                        "META_SYSTEM_USER_ACCESS_TOKEN": self.meta_system_user_access_token,
                    }.items()
                    if not value or str(value).startswith("local-")
                ]
                if missing:
                    raise ValueError(
                        "Meta WhatsApp configuration is incomplete: " + ", ".join(missing)
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
