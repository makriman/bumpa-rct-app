"""Private voice service. Public ingress stays on the signed API webhook."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.routes.whatsapp import _read_bounded_body, _valid_signature
from app.voice.gateway import CallGateway
from app.voice.signaling import call_events


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # Third-party ICE logs can contain IP addresses; never emit SDP or peer PII.
    for name in ("aioice", "aiortc", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    settings = get_settings()
    if not settings.whatsapp_calling_enabled:
        raise RuntimeError("WHATSAPP_CALLING_ENABLED is required for the voice service")
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    await redis.ping()
    application.state.gateway = CallGateway(settings, redis)
    try:
        yield
    finally:
        await application.state.gateway.shutdown()
        await redis.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    gateway = request.app.state.gateway
    if gateway.stopping:
        raise HTTPException(status_code=503, detail="Stopping")
    try:
        await gateway.redis.ping()
    except Exception:
        raise HTTPException(status_code=503, detail="Call state unavailable") from None
    return {"status": "ready"}


@app.post("/webhooks/whatsapp/calls")
async def receive(request: Request) -> dict[str, str]:
    settings = get_settings()
    raw = await _read_bounded_body(request)
    if not _valid_signature(
        raw, request.headers.get("x-hub-signature-256"), settings.effective_meta_app_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = json.loads(raw)
        events = call_events(payload, settings)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid call payload") from None
    try:
        # Each setup has its own deadline; concurrent dispatch keeps a batched
        # webhook within Meta's response timeout. Terminate events win in Redis.
        import asyncio

        await asyncio.gather(*(request.app.state.gateway.handle(event) for event in events))
    except Exception:
        logging.getLogger("bumpabestie.voice").warning("voice_webhook_failed")
        raise HTTPException(status_code=503, detail="Call handling unavailable") from None
    return {"status": "accepted"}
