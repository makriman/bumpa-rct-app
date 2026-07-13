from __future__ import annotations

import os
import re
import sys

from redis import Redis

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_SAFE_KEY_PREFIX = re.compile(r"[A-Za-z0-9._-]+")
_REDIS_TIMEOUT_SECONDS = 1


def _runtime_enabled() -> bool:
    value = os.getenv("ASYNC_RUNTIME_ENABLED", "false").strip().lower()
    if value in _ENABLED_VALUES:
        return True
    if value in _DISABLED_VALUES:
        return False
    raise ValueError("ASYNC_RUNTIME_ENABLED must be true or false")


def _heartbeat_key(service: str) -> str:
    prefix = os.getenv("ASYNC_QUEUE_KEY_PREFIX", "bumpabestie")
    if not _SAFE_KEY_PREFIX.fullmatch(prefix):
        raise ValueError("ASYNC_QUEUE_KEY_PREFIX contains invalid characters")
    return f"{prefix}:health:{service}"


def _heartbeat_exists(service: str) -> bool:
    """Return the heartbeat state without exposing Redis errors or configuration."""
    try:
        if not _runtime_enabled():
            return False
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        if not redis_url:
            return False
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            health_check_interval=0,
            retry_on_timeout=False,
        )
        try:
            return client.exists(_heartbeat_key(service)) == 1
        finally:
            client.close()
    except Exception:
        # A health command must fail closed and must never print an exception
        # containing a credential-bearing Redis URL.
        return False


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"worker", "scheduler"}:
        raise SystemExit("Usage: python -m app.jobs.health worker|scheduler")
    if not _heartbeat_exists(sys.argv[1]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
