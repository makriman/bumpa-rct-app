from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.providers.hermes import HermesClient, HermesEndpoint
from app.services.business_tools import resolve_period


def test_explicit_dates_are_not_silently_replaced_by_current_month() -> None:
    with patch(
        "app.services.business_tools.tenant_store_context",
        return_value=(None, "Africa/Lagos", "NGN"),
    ):
        result = resolve_period(
            None,
            tenant_id="synthetic",
            date_from=date(2026, 7, 1),
            date_to=date(2026, 7, 7),
            now=datetime(2026, 10, 27, tzinfo=UTC),
        )
    assert (result.date_from, result.date_to, result.name) == (
        date(2026, 7, 1),
        date(2026, 7, 7),
        "custom",
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": date(2026, 7, 1)},
        {"period": "today", "date_from": date(2026, 7, 1), "date_to": date(2026, 7, 7)},
    ],
)
def test_incomplete_or_conflicting_periods_fail_explicitly(kwargs: dict) -> None:
    with patch(
        "app.services.business_tools.tenant_store_context",
        return_value=(None, "Africa/Lagos", "NGN"),
    ):
        with pytest.raises(ValueError):
            resolve_period(None, tenant_id="synthetic", **kwargs)


@pytest.mark.parametrize(
    "history", [[], [{"role": "assistant", "content": "Saved. Start with one clear offer."}]]
)
def test_canonical_history_wins_over_stale_native_binding(history: list[dict[str, str]]) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/v1/runs"
            body = json.loads(request.content)
            assert body["conversation_history"] == history
            assert "Current action context" not in json.dumps(body["conversation_history"])
            assert "business" in body["instructions"]
            if not history:
                assert "session_id" not in body
                assert "x-hermes-session-key" not in request.headers
            return httpx.Response(
                202,
                headers={"X-Bumpa-Context-Contract": "1"},
                json={"run_id": "run_synthetic", "status": "queued"},
            )
        return httpx.Response(
            200,
            json={
                "run_id": "run_synthetic",
                "status": "completed",
                "session_id": "clean-session",
                "output": "Let's improve that offer.",
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
        )

    settings = Settings(
        _env_file=None,
        app_env="test",
        hermes_base_internal_host="http://hermes",
        hermes_profile_port_start=8700,
        hermes_profile_port_end=8705,
    )
    result = HermesClient(settings, transport=httpx.MockTransport(handler)).respond(
        HermesEndpoint("business-a", "http://hermes:8700/v1", "synthetic-key"),
        message="What does this mean?",
        business_context="business: synthetic",
        history=history,
        session_id="stale-native",
        session_key="legacy-key",
    )
    assert result.session_id == "clean-session"
    assert result.content == "Let's improve that offer."
    assert len(requests) == 2
