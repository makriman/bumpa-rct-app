from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.providers.hermes import (
    WHATSAPP_RESPONSE_POLICY,
    HermesClient,
    HermesEndpoint,
)


@pytest.mark.parametrize("channel", [None, "web", "whatsapp"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("session_id", [None, "existing-session"])
def test_reply_style_is_channel_specific_on_new_and_resumed_requests(
    tmp_path: Path, channel: str | None, stream: bool, session_id: str | None
) -> None:
    requests: list[httpx.Request] = []
    # Explicitly requested long answers must survive intact; brevity is a generation
    # instruction, not a character cut-off that could remove caveats or citations.
    answer = "A complete requested draft. " * 100

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if stream:
            return httpx.Response(
                200,
                text="data: "
                + json.dumps({"choices": [{"delta": {"content": answer}}]})
                + "\n\ndata: [DONE]\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": answer}}]}
        )

    client = HermesClient(
        Settings(app_env="test", hermes_profile_root=tmp_path, hermes_context_mode="legacy_native"),
        transport=httpx.MockTransport(handler),
    )
    options = {
        "message": "Write the full draft",
        "business_context": "Bounded store facts. " * 10_000,
        "history": [{"role": "assistant", "content": "Earlier answer"}],
        "session_id": session_id,
        "channel": channel,
    }
    endpoint = HermesEndpoint("tenant_profile", "http://hermes:8700/v1", "private-key")
    if stream:
        result = client.respond_stream(endpoint, **options, on_event=lambda *_: None)
    else:
        result = client.respond(endpoint, **options)

    messages = json.loads(requests[0].content)["messages"]
    assert ({"role": "system", "content": WHATSAPP_RESPONSE_POLICY} in messages) == (
        channel == "whatsapp"
    )
    assert ("Earlier answer" in str(messages)) == (session_id is None)
    assert result.content == answer.strip()


@pytest.mark.parametrize("channel", [None, "web", "whatsapp"])
@pytest.mark.parametrize("stream", [False, True])
def test_canonical_runs_keep_channel_style_typed_and_never_reuse_native_history(channel, stream):
    payloads = []
    answer = "A complete requested answer. " * 100

    def handler(request):
        if request.method == "POST":
            payloads.append(json.loads(request.content))
            return httpx.Response(
                202, headers={"X-Bumpa-Context-Contract": "1"}, json={"run_id": "style_run"}
            )
        return httpx.Response(
            200, json={"run_id": "style_run", "status": "completed", "output": answer}
        )

    client = HermesClient(
        Settings(_env_file=None, app_env="test"), transport=httpx.MockTransport(handler)
    )
    endpoint = HermesEndpoint("synthetic-profile", "http://hermes:8700/v1", "synthetic-profile-key")
    options = dict(
        message="Write a full answer",
        business_context="Bounded current facts",
        channel=channel,
        history=[{"role": "assistant", "content": "Earlier accepted answer"}],
        session_id="old-native",
        session_key="old-affinity",
    )
    result = (
        client.respond_stream(endpoint, **options, on_event=lambda *_: None)
        if stream
        else client.respond(endpoint, **options)
    )
    payload = payloads[0]
    assert (WHATSAPP_RESPONSE_POLICY in payload["instructions"]) == (channel == "whatsapp")
    assert payload["conversation_history"] == options["history"]
    assert payload["input"] == [{"role": "user", "content": "Write a full answer"}]
    assert "session_id" not in payload and "previous_response_id" not in payload
    assert result.content == answer.strip()
