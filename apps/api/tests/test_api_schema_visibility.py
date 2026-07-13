from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_local_api_documentation_and_schema_are_available(monkeypatch: pytest.MonkeyPatch) -> None:
    local_settings = get_settings().model_copy(update={"app_env": "local"})
    monkeypatch.setattr("app.main.get_settings", lambda: local_settings)
    client = TestClient(create_app())

    docs_response = client.get("/docs")
    schema_response = client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert "text/html" in docs_response.headers["content-type"]
    assert schema_response.status_code == 200
    assert schema_response.json()["info"]["title"] == local_settings.app_name


@pytest.mark.parametrize("app_env", ["staging", "production"])
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_nonlocal_api_documentation_and_schema_are_not_exposed(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    path: str,
) -> None:
    hardened_settings = get_settings().model_copy(update={"app_env": app_env})
    monkeypatch.setattr("app.main.get_settings", lambda: hardened_settings)
    client = TestClient(create_app())

    assert client.get(path).status_code == 404
