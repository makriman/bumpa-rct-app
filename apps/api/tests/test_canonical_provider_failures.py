from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.providers.hermes import (
    HermesCircuitBreaker,
    HermesClient,
    HermesEndpoint,
    HermesError,
    HermesInvalidResponse,
    HermesUnavailable,
)

ENDPOINT = HermesEndpoint("synthetic-profile", "http://hermes:8700/v1", "synthetic-profile-key")


def client(handler):
    return HermesClient(
        Settings(_env_file=None, app_env="test"),
        transport=httpx.MockTransport(handler),
        breaker=HermesCircuitBreaker(threshold=3, recovery_seconds=30),
    )


@pytest.mark.parametrize(
    "status",
    [
        [],
        {"run_id": "different-run", "status": "completed", "output": "wrong reply"},
        {"run_id": "run_test", "status": "completed", "output": ""},
        {"run_id": "run_test", "status": "completed", "output": []},
        {"run_id": "run_test", "status": "completed", "output": "answer", "usage": []},
        {"run_id": "run_test", "status": "completed", "output": "answer", "usage": "secret"},
        {"run_id": "run_test", "status": "completed", "output": "answer", "session_id": 7},
        {"run_id": "run_test", "status": "completed", "output": "answer", "session_id": "x" * 161},
        {"run_id": "run_test", "status": "failed", "error": "private upstream detail"},
        {"run_id": "run_test", "status": "cancelled"},
        {"run_id": "run_test", "status": "waiting_for_approval"},
    ],
)
def test_non_success_or_misbound_run_never_becomes_a_merchant_answer(status):

    def handler(request):
        return (
            httpx.Response(
                202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": "run_test"}
            )
            if request.method == "POST"
            else httpx.Response(200, json=status)
        )

    with pytest.raises(HermesError) as failure:
        client(handler).respond(ENDPOINT, message="hello", business_context="synthetic", history=[])
    assert "private upstream detail" not in str(failure.value)


@pytest.mark.parametrize("admission", [{}, [], {"run_id": "../another-profile"}, {"run_id": 6}])
def test_invalid_admission_does_not_poll_an_untrusted_path(admission):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(202, headers={"X-Bumpa-Context-Contract": "1"}, json=admission)

    with pytest.raises(HermesInvalidResponse):
        client(handler).respond(ENDPOINT, message="hello", business_context="synthetic")
    assert len(calls) == 1


def test_queued_run_records_admission_then_only_returns_committed_output():
    states = iter(["queued", "running", "completed"])
    admissions = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": "run_test"}
            )
        return httpx.Response(
            200, json={"run_id": "run_test", "status": next(states), "output": "committed answer"}
        )

    with patch("app.providers.hermes.sleep"):
        result = client(handler).respond(
            ENDPOINT, message="hello", business_context="synthetic", on_admitted=admissions.append
        )
    assert result.content == "committed answer"
    assert admissions == ["run_test"]


def test_deadline_requests_stop_without_reporting_success():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": "run_test"}
        )

    with patch("app.providers.hermes.monotonic", side_effect=[0, 0, 0, 1000]):
        with pytest.raises(HermesUnavailable, match="deadline"):
            client(handler).respond(ENDPOINT, message="hello", business_context="synthetic")
    assert calls == ["/v1/runs", "/v1/runs/run_test/stop"]


def test_citation_repair_is_internal_context_not_a_fabricated_user_turn():
    posts = []
    admissions = []

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            posts.append(body)
            if len(posts) == 2:
                assert body["input"] == posts[0]["input"]
                assert body["conversation_history"] == posts[0]["conversation_history"]
                assert "Internal citation repair" in body["instructions"]
            return httpx.Response(
                202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": f"run_{len(posts)}"}
            )
        answer = (
            "draft lacking citations"
            if len(posts) == 1
            else "Verified. https://example.com/a https://example.com/b"
        )
        return httpx.Response(
            200,
            json={
                "run_id": f"run_{len(posts)}",
                "status": "completed",
                "output": answer,
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    result = client(handler).respond(
        ENDPOINT,
        message="Give current official sources",
        business_context="synthetic",
        on_admitted=admissions.append,
    )
    assert result.input_tokens == 4 and result.output_tokens == 6 and (result.total_tokens == 10)
    assert admissions == ["run_1", "run_2"]


def test_stream_only_emits_public_answer_after_validation():
    events = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": "run_test"}
            )
        return httpx.Response(
            200,
            json={
                "run_id": "run_test",
                "status": "completed",
                "output": "public answer",
                "private_tool_args": "never forward",
            },
        )

    result = client(handler).respond_stream(
        ENDPOINT,
        message="hello",
        business_context="synthetic",
        on_event=lambda kind, event: events.append((kind, event)),
    )
    assert result.content == "public answer"
    assert events == [("message.delta", {"delta": "public answer"})]


def test_unmatched_hermes_image_cannot_return_a_merchant_answer():
    def handler(request):
        return httpx.Response(202, json={"run_id": "run_test"})

    with pytest.raises(HermesInvalidResponse, match="context contract"):
        client(handler).respond(ENDPOINT, message="hello", business_context="synthetic")


@pytest.mark.parametrize("citation_repair", [False, True])
def test_late_completion_and_internal_repair_share_one_turn_deadline(citation_repair):
    moment = [0.0]
    posts = []
    stops = []

    def handler(request):
        if request.url.path.endswith("/stop"):
            stops.append(request.url.path)
            return httpx.Response(200, json={"status": "cancelled"})
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(
                202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": f"run_{len(posts)}"}
            )
        if citation_repair and len(posts) == 1:
            moment[0] = 89.0
            return httpx.Response(
                200,
                json={"run_id": "run_1", "status": "completed", "output": "Uncited first draft"},
            )
        moment[0] = 91.0
        return httpx.Response(
            200,
            json={
                "run_id": f"run_{len(posts)}",
                "status": "completed",
                "output": "Late answer with https://example.com/one https://example.org/two",
            },
        )

    with patch("app.providers.hermes.monotonic", side_effect=lambda: moment[0]):
        with pytest.raises(HermesUnavailable, match="deadline"):
            client(handler).respond(
                ENDPOINT,
                message="Should I expand to Ghana?" if citation_repair else "hello",
                business_context="Synthetic current facts",
            )
    assert len(posts) == (2 if citation_repair else 1)
    assert stops == [f"/v1/runs/run_{len(posts)}/stop"]
