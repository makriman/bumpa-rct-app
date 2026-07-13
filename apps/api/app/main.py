from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from time import monotonic
from uuid import RFC_4122, UUID, uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, correlation_id_var
from app.core.request_context import audit_request_context_var, build_audit_request_context
from app.db.session import SessionLocal, create_schema, set_security_context
from app.jobs.runtime import AsyncRuntimeConfig, RedisHealthProbe
from app.routes import (
    admin,
    auth,
    bumpa,
    chat,
    hermes,
    mcp,
    mcp_admin,
    onboarding,
    research,
    settings,
    tenants,
    whatsapp,
)
from app.services.production_readiness import check_production_readiness
from app.services.seed import seed_demo

request_logger = logging.getLogger("bumpabestie.http")


@lru_cache(maxsize=4)
def _redis_health_probe(config: AsyncRuntimeConfig) -> RedisHealthProbe:
    """Reuse the thread-safe probe so an outage cannot force repeated DNS lookups."""

    return RedisHealthProbe(config)


def _safe_route_template(request: Request) -> str:
    """Return only the declared route template; never log raw paths or query strings."""

    route = request.scope.get("route")
    path_template = getattr(route, "path", "<unmatched>")
    return path_template if isinstance(path_template, str) else "<unmatched>"


def _request_correlation_id(value: str | None) -> str:
    """Preserve only canonical RFC 4122 UUIDv4 IDs across trusted service hops."""

    if value is not None:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if parsed.version == 4 and parsed.variant == RFC_4122 and str(parsed) == value:
                return value
    return str(uuid4())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings_config = get_settings()
    if settings_config.is_local:
        create_schema()
    if settings_config.is_local and settings_config.seed_demo_data:
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            seed_demo(db, settings_config)
    yield


def create_app(*, settings_config: Settings | None = None) -> FastAPI:
    configure_logging()
    config = settings_config or get_settings()
    application = FastAPI(
        title=config.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if config.is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.is_local else None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.effective_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "If-Match",
            "Idempotency-Key",
            "X-Tenant-ID",
            "X-Correlation-ID",
        ],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Correlation metadata is emitted on every log line. Preserve a canonical
        # service-hop UUIDv4, but replace all other caller-controlled values so a
        # phone, OTP, token, or noncanonical identifier cannot enter logs.
        correlation_id = _request_correlation_id(request.headers.get("x-correlation-id"))
        correlation_token = correlation_id_var.set(correlation_id)
        audit_context_token = audit_request_context_var.set(
            build_audit_request_context(
                client_host=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        )
        started = monotonic()
        try:
            response = await call_next(request)
            duration_ms = round((monotonic() - started) * 1000, 1)
            request_logger.info(
                "request_completed",
                extra={
                    "duration_ms": duration_ms,
                    "method": request.method,
                    "path": _safe_route_template(request),
                    "status_code": response.status_code,
                },
            )
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
            return response
        except Exception:
            request_logger.exception(
                "request_failed",
                extra={
                    "duration_ms": round((monotonic() - started) * 1000, 1),
                    "method": request.method,
                    "path": _safe_route_template(request),
                    "status_code": 500,
                },
            )
            raise
        finally:
            audit_request_context_var.reset(audit_context_token)
            correlation_id_var.reset(correlation_token)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "correlation_id": correlation_id_var.get(),
                    "fields": [
                        {
                            "location": list(item["loc"]),
                            "message": item["msg"],
                            "type": item["type"],
                        }
                        for item in exc.errors()
                    ],
                }
            },
        )

    @application.get("/health/live", tags=["health"])
    def health_live() -> dict:
        return {"status": "ok", "service": "api"}

    @application.get("/health/ready", tags=["health"])
    def health_ready(response: Response, settings_config: Settings = Depends(get_settings)) -> dict:
        readiness = check_production_readiness(
            settings_config,
            redis_probe_factory=_redis_health_probe,
        )
        response.status_code = readiness.http_status
        return readiness.payload()

    @application.get("/health", include_in_schema=False)
    def health_compatibility() -> dict:
        return health_live()

    @application.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": config.app_name,
            "status": "ok",
            "docs": "/docs" if config.is_local else None,
        }

    for module in (
        auth,
        tenants,
        settings,
        chat,
        bumpa,
        hermes,
        mcp,
        admin,
        mcp_admin,
        onboarding,
        research,
    ):
        application.include_router(module.router, prefix=config.api_prefix)
    application.include_router(whatsapp.router)
    return application


app = create_app()
