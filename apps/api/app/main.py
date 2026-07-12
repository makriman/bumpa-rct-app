from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, correlation_id_var
from app.db.session import SessionLocal, create_schema, set_security_context
from app.routes import admin, auth, bumpa, chat, hermes, mcp, research, settings, tenants, whatsapp
from app.services.seed import seed_demo


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


def create_app() -> FastAPI:
    configure_logging()
    config = get_settings()
    application = FastAPI(
        title=config.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if config.is_local else None,
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.effective_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Tenant-ID", "X-Correlation-ID"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
        token = correlation_id_var.set(correlation_id)
        started = monotonic()
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Server-Timing"] = f"app;dur={(monotonic() - started) * 1000:.1f}"
        return response

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
    def health_ready(settings_config: Settings = Depends(get_settings)) -> dict:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "database": "ok",
            "providers": {
                "whatsapp": settings_config.whatsapp_backend,
                "bumpa": settings_config.bumpa_backend,
                "agent": settings_config.agent_backend,
            },
        }

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

    for module in (auth, tenants, settings, chat, bumpa, hermes, mcp, admin, research):
        application.include_router(module.router, prefix=config.api_prefix)
    application.include_router(whatsapp.router)
    return application


app = create_app()
