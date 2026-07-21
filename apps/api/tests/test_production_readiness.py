from __future__ import annotations

from collections.abc import Callable

from redis.exceptions import RedisError

from app.core.config import Settings
from app.jobs.runtime import AsyncRuntimeConfig
from app.services.production_readiness import (
    check_production_readiness,
    production_provider_selectors_ready,
    provider_selectors,
)


def _runtime(*, enabled: bool = True) -> AsyncRuntimeConfig:
    return AsyncRuntimeConfig(
        enabled=enabled,
        redis_url="redis://unused",
        queue_name="default",
        queue_key_prefix="readiness-test",
        heartbeat_ttl_seconds=45,
        pop_timeout_seconds=1,
        scheduler_interval_seconds=1,
        dispatch_batch_size=10,
        redispatch_seconds=60,
        retry_base_seconds=1,
        retry_max_seconds=10,
        stale_lock_seconds=60,
    )


class _Probe:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self._snapshot = snapshot

    def health_snapshot(self) -> dict[str, object]:
        return self._snapshot


def _factory(snapshot: dict[str, object]) -> Callable[[AsyncRuntimeConfig], _Probe]:
    return lambda _config: _Probe(snapshot)


def test_live_provider_selectors_are_typed_for_onboarding() -> None:
    live = Settings(
        app_env="test",
        whatsapp_backend="meta",
        bumpa_backend="bumpa",
        agent_backend="hermes",
    )
    selectors = provider_selectors(live)

    assert selectors.ready is True
    assert selectors.payload() == {
        "whatsapp": "meta",
        "bumpa": "bumpa",
        "agent": "hermes",
    }
    assert production_provider_selectors_ready(live) is True
    assert production_provider_selectors_ready(Settings(app_env="test")) is False

    test_only = Settings(
        app_env="test",
        auth_login_mode="disabled",
        whatsapp_backend="meta",
        meta_waba_id="2234567890",
        meta_phone_number_id="3234567890",
        meta_primary_sender_enabled=False,
        meta_test_sender_verification_mode="inbound_replies_only",
        meta_test_sender_waba_id="423456789012345",
        meta_test_sender_phone_number_id="523456789012345",
        meta_test_sender_display_phone_e164="+15550102030",
        bumpa_backend="bumpa",
        agent_backend="hermes",
    )
    test_only_selectors = provider_selectors(test_only)
    assert test_only_selectors.ready is False
    assert test_only_selectors.payload()["whatsapp"] == "meta_test_reply_only"
    assert production_provider_selectors_ready(test_only) is False


def test_readiness_reports_healthy_database_queue_and_live_selectors() -> None:
    database_calls = 0

    def database_probe() -> None:
        nonlocal database_calls
        database_calls += 1

    result = check_production_readiness(
        Settings(
            app_env="test",
            whatsapp_backend="meta",
            bumpa_backend="bumpa",
            agent_backend="hermes",
        ),
        database_probe=database_probe,
        async_config=_runtime(),
        redis_probe_factory=_factory(
            {
                "redis": "ok",
                "worker": "ok",
                "scheduler": "ok",
                "queued_wakeups": 2,
            }
        ),
    )

    assert database_calls == 1
    assert result.ready is True
    assert result.http_status == 200
    assert result.providers.ready is True
    assert result.payload() == {
        "status": "ready",
        "database": "ok",
        "async_runtime": {
            "enabled": True,
            "redis": "ok",
            "worker": "ok",
            "scheduler": "ok",
            "queued_wakeups": 2,
        },
        "providers": {"whatsapp": "meta", "bumpa": "bumpa", "agent": "hermes"},
    }


def test_queue_outage_fails_closed_without_exposing_exception_text() -> None:
    def unavailable_queue(_config: AsyncRuntimeConfig) -> _Probe:
        raise RedisError("private queue endpoint and credential")

    result = check_production_readiness(
        Settings(app_env="test"),
        database_probe=lambda: None,
        async_config=_runtime(),
        redis_probe_factory=unavailable_queue,
    )

    assert result.ready is False
    assert result.http_status == 503
    assert result.payload()["async_runtime"] == {
        "enabled": True,
        "redis": "unavailable",
        "worker": "unknown",
        "scheduler": "unknown",
        "queued_wakeups": None,
    }
    assert "private queue" not in repr(result.payload())


def test_database_outage_and_disabled_async_runtime_are_typed_fail_closed() -> None:
    def unavailable_database() -> None:
        raise OSError("private database endpoint")

    result = check_production_readiness(
        Settings(app_env="test"),
        database_probe=unavailable_database,
        async_config=_runtime(enabled=False),
    )

    assert result.ready is False
    assert result.http_status == 503
    assert result.payload() == {
        "status": "not_ready",
        "database": "unavailable",
        "async_runtime": {"enabled": False},
        "providers": {"whatsapp": "mock", "bumpa": "mock", "agent": "mock"},
    }
    assert "private database" not in repr(result.payload())
