from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from redis.exceptions import RedisError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import AsyncJob
from app.db.session import SessionLocal, set_security_context
from app.jobs.runtime import AsyncRuntimeConfig, RedisHealthProbe

DatabaseStatus = Literal["ok", "unavailable"]
WhatsappSelector = Literal["mock", "disabled", "meta", "meta_test_reply_only"]


class HealthSnapshotProbe(Protocol):
    def health_snapshot(self) -> dict[str, object]: ...


RedisProbeFactory = Callable[[AsyncRuntimeConfig], HealthSnapshotProbe]
DatabaseProbe = Callable[[], None]


@dataclass(frozen=True)
class ProviderSelectors:
    whatsapp: WhatsappSelector
    bumpa: Literal["mock", "disabled", "bumpa"]
    agent: Literal["mock", "disabled", "hermes"]

    @property
    def ready(self) -> bool:
        """Return whether every provider is the production implementation."""

        return self.whatsapp == "meta" and self.bumpa == "bumpa" and self.agent == "hermes"

    def payload(self) -> dict[str, str]:
        return {
            "whatsapp": self.whatsapp,
            "bumpa": self.bumpa,
            "agent": self.agent,
        }


@dataclass(frozen=True)
class ProductionReadiness:
    """Typed infrastructure and provider state shared by HTTP and onboarding."""

    ready: bool
    database: DatabaseStatus
    async_runtime: Mapping[str, object]
    providers: ProviderSelectors

    @property
    def http_status(self) -> Literal[200, 503]:
        return 200 if self.ready else 503

    def payload(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "database": self.database,
            "async_runtime": dict(self.async_runtime),
            "providers": self.providers.payload(),
        }


def provider_selectors(settings: Settings) -> ProviderSelectors:
    whatsapp: WhatsappSelector = settings.whatsapp_backend
    if settings.whatsapp_backend == "meta" and not settings.meta_primary_sender_enabled:
        whatsapp = "meta_test_reply_only"
    return ProviderSelectors(
        whatsapp=whatsapp,
        bumpa=settings.bumpa_backend,
        agent=settings.agent_backend,
    )


def production_provider_selectors_ready(settings: Settings) -> bool:
    return provider_selectors(settings).ready


def check_production_readiness(
    settings: Settings,
    *,
    database_probe: DatabaseProbe | None = None,
    async_config: AsyncRuntimeConfig | None = None,
    redis_probe_factory: RedisProbeFactory | None = None,
) -> ProductionReadiness:
    """Check durable dependencies without treating provider selection as health.

    Provider selectors remain informational for the public readiness endpoint.
    Onboarding additionally requires ``result.providers.ready`` before activating
    a tenant, while disabled providers can still be a healthy containment mode.
    """

    try:
        (database_probe or _probe_database)()
    except (SQLAlchemyError, OSError):
        database: DatabaseStatus = "unavailable"
    else:
        database = "ok"

    runtime = async_config or AsyncRuntimeConfig.from_env()
    async_health: dict[str, object] = {"enabled": runtime.enabled}
    async_ready = True
    if runtime.enabled:
        factory = redis_probe_factory or RedisHealthProbe
        try:
            snapshot = factory(runtime).health_snapshot()
        except (RedisError, OSError):
            snapshot = {
                "redis": "unavailable",
                "worker": "unknown",
                "scheduler": "unknown",
                "queued_wakeups": None,
            }
        async_health.update(snapshot)
        if database == "ok" and database_probe is None:
            async_health.update(whatsapp_queue_delay(runtime.queue_name))
        async_ready = all(
            snapshot.get(service) == "ok" for service in ("redis", "worker", "scheduler")
        )

    return ProductionReadiness(
        ready=database == "ok" and async_ready,
        database=database,
        async_runtime=async_health,
        providers=provider_selectors(settings),
    )


def _probe_database() -> None:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))


def whatsapp_queue_delay(queue_name: str = "default") -> dict[str, object]:
    """Expose durable work age; Redis ID counts/heartbeats cannot prove latency."""
    now = utcnow()
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        oldest = db.scalar(
            select(func.min(AsyncJob.available_at)).where(
                AsyncJob.queue_name == queue_name,
                AsyncJob.kind.startswith("whatsapp."),
                AsyncJob.status.in_(("pending", "queued", "retry")),
                AsyncJob.available_at <= now,
            )
        )
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=now.tzinfo)
    age = max(0, int((now - oldest).total_seconds())) if oldest is not None else 0
    return {
        "whatsapp_oldest_wait_seconds": age,
        "whatsapp_queue": "delayed" if age > 60 else "ok",
    }
