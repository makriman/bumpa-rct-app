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
        Settings(app_env="test", hermes_profile_root=tmp_path),
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
