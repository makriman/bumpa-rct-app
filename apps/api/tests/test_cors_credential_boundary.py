from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.cors import credentialed_cors_origins
from app.main import create_app


def test_credentialed_cors_rejects_wildcard_and_null_origins() -> None:
    for unsafe in ("*", "null", "NULL", "https://app.example.test/*"):
        settings = get_settings().model_copy(
            update={"cors_origins": [unsafe], "cors_allowed_origins": None}
        )
        with pytest.raises(ValueError, match="wildcard or null"):
            credentialed_cors_origins(settings)


def test_credentialed_cors_rejects_non_origin_values() -> None:
    settings = get_settings().model_copy(
        update={
            "cors_origins": ["https://app.example.test/path"],
            "cors_allowed_origins": None,
        }
    )
    with pytest.raises(ValueError, match="exact scheme-host-port"):
        credentialed_cors_origins(settings)


def test_non_local_credentialed_cors_keeps_only_exact_https_origins() -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "cors_allowed_origins": "http://localhost:3000, https://App.Example.test:443, https://admin.example.test:8443",
            "cors_origins": ["https://ignored.example.test"],
        }
    )

    assert credentialed_cors_origins(settings) == [
        "https://app.example.test",
        "https://admin.example.test:8443",
    ]


def test_create_app_does_not_install_a_wildcard_cors_middleware() -> None:
    settings = get_settings().model_copy(
        update={"cors_origins": ["https://app.example.test", "*"], "cors_allowed_origins": None}
    )
    with pytest.raises(ValueError, match="wildcard or null"):
        create_app(settings_config=settings)


def test_cors_preflight_reflects_only_configured_origins(client: TestClient) -> None:
    origin = get_settings().effective_cors_origins[0]
    allowed = client.options(
        "/v1/auth/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == origin
    assert allowed.headers.get("access-control-allow-credentials") == "true"

    for hostile in ("https://attacker.test", "null", "*"):
        rejected = client.options(
            "/v1/auth/me",
            headers={
                "Origin": hostile,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert rejected.headers.get("access-control-allow-origin") is None


def test_production_cors_middleware_omits_insecure_origins() -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "expose_local_otp": False,
            "seed_demo_data": False,
            "cors_allowed_origins": None,
            "cors_origins": ["http://localhost:3000", "https://app.example.test"],
        }
    )
    with TestClient(create_app(settings_config=settings)) as client:
        insecure = client.options(
            "/health/live",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        secure = client.options(
            "/health/live",
            headers={
                "Origin": "https://app.example.test",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert insecure.headers.get("access-control-allow-origin") is None
    assert secure.headers.get("access-control-allow-origin") == "https://app.example.test"
