from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.services import chat
from tests.conftest import auth_headers


@pytest.mark.parametrize("freshness", [None, datetime(2026, 11, 25, 10, 12, 13, 456789, UTC)])
def test_retry_preserves_context_freshness_even_when_it_differs_from_input_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, freshness: datetime | None
) -> None:
    headers = auth_headers(client, "+2348012345678")
    # A concurrent import can change context after input admission. Rebuilding
    # freshness at the input timestamp cannot reproduce the accepted response.
    monkeypatch.setattr(
        chat, "build_business_context", lambda *args: ("Synthetic context", freshness)
    )
    payload = {
        "message": "Hello",
        "client_message_id": f"freshness-replay-race-{'dated' if freshness else 'unavailable'}",
    }
    first = client.post("/v1/chat/web", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    expected = freshness.isoformat().replace("+00:00", "Z") if freshness else None
    assert first.json()["data_freshness"] == expected
    monkeypatch.setattr(
        chat, "build_business_context", lambda *args: pytest.fail("Replay regenerated context")
    )
    replay = client.post("/v1/chat/web", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
