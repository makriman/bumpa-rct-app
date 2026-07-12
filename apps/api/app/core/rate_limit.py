from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import cast

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.crypto import secret_hash

_FIXED_WINDOW_SCRIPT = """
local window = tonumber(ARGV[1])
local retry_after = 0
for index, key in ipairs(KEYS) do
  local current = tonumber(redis.call('GET', key) or '0')
  local limit = tonumber(ARGV[index + 1])
  if current >= limit then
    local ttl = redis.call('TTL', key)
    if ttl < 1 then
      redis.call('EXPIRE', key, window)
      ttl = window
    end
    if ttl > retry_after then retry_after = ttl end
  end
end
if retry_after > 0 then
  return {0, retry_after}
end
for _, key in ipairs(KEYS) do
  local value = redis.call('INCR', key)
  if value == 1 then redis.call('EXPIRE', key, window) end
end
return {1, 0}
"""


class RateLimitUnavailable(RuntimeError):
    pass


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after = retry_after


@lru_cache(maxsize=8)
def _redis_client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )


def client_ip(request: Request) -> str:
    """Return a stable, validated address without trusting arbitrary text headers here.

    Uvicorn resolves the edge-provided forwarding header before constructing the
    Request. Direct browser traffic is therefore the edge-derived address while
    same-origin BFF traffic is the private web-service address.
    """

    candidate = request.client.host if request.client else "unknown"
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        return "unknown"


def enforce_auth_rate_limit(
    request: Request,
    *,
    phone_e164: str,
    operation: str,
    settings: Settings,
    client: Redis | None = None,
) -> None:
    if not settings.effective_auth_rate_limit_enabled:
        return
    if operation == "request":
        phone_limit = settings.auth_request_phone_limit
        ip_limit = settings.auth_request_ip_limit
    elif operation == "verify":
        phone_limit = settings.auth_verify_phone_limit
        ip_limit = settings.auth_verify_ip_limit
    else:
        raise ValueError("Unsupported authentication rate-limit operation")

    phone_digest = secret_hash(f"rate-limit:phone:{phone_e164}", settings.otp_secret)
    address_digest = secret_hash(f"rate-limit:ip:{client_ip(request)}", settings.otp_secret)
    prefix = f"bumpabestie:auth:{operation}"
    keys = [f"{prefix}:phone:{phone_digest}", f"{prefix}:ip:{address_digest}"]
    try:
        _consume_fixed_window(
            settings.redis_url,
            keys=keys,
            limits=[phone_limit, ip_limit],
            window_seconds=settings.auth_rate_limit_window_seconds,
            client=client,
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication service is temporarily unavailable",
        ) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts; try again later",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


def consume_operation_rate_limit(
    settings: Settings,
    *,
    operation: str,
    scopes: dict[str, str],
    limit: int,
    window_seconds: int,
    client: Redis | None = None,
) -> None:
    """Consume a fail-closed, privacy-preserving budget for a costly operation."""

    if not settings.effective_operation_rate_limit_enabled:
        return
    keys = [
        f"bumpabestie:operation:{operation}:{scope}:{secret_hash(f'{operation}:{scope}:{value}', settings.otp_secret)}"
        for scope, value in sorted(scopes.items())
    ]
    _consume_fixed_window(
        settings.redis_url,
        keys=keys,
        limits=[limit] * len(keys),
        window_seconds=window_seconds,
        client=client,
    )


def enforce_operation_rate_limit(
    settings: Settings,
    *,
    operation: str,
    scopes: dict[str, str],
    limit: int,
    window_seconds: int,
    client: Redis | None = None,
) -> None:
    try:
        consume_operation_rate_limit(
            settings,
            operation=operation,
            scopes=scopes,
            limit=limit,
            window_seconds=window_seconds,
            client=client,
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Service budget is temporarily unavailable"
        ) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="This operation is temporarily rate limited",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


def _consume_fixed_window(
    redis_url: str,
    *,
    keys: list[str],
    limits: list[int],
    window_seconds: int,
    client: Redis | None = None,
) -> None:
    if not keys or len(keys) != len(limits):
        raise ValueError("Rate-limit keys and limits must be non-empty and aligned")
    redis_client = client or _redis_client(redis_url)
    try:
        raw = redis_client.eval(
            _FIXED_WINDOW_SCRIPT,
            len(keys),
            *keys,
            str(window_seconds),
            *(str(limit) for limit in limits),
        )
        result = cast(list[int], raw)
    except (RedisError, OSError) as exc:
        raise RateLimitUnavailable("Rate-limit store is unavailable") from exc
    if not result or int(result[0]) != 1:
        retry_after = max(1, int(result[1]) if len(result) > 1 else 1)
        raise RateLimitExceeded(retry_after)
