"""Real built API HermesClient calls the actual built Hermes over internal HTTP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import httpx

from app.core.config import Settings
from app.providers.hermes import HermesClient, HermesEndpoint

KEY = "dca217b5449f834bd6081c653be9c0b56970a4a2659d01c572ff22c8fb2be380a"  # gitleaks:allow -- reviewed synthetic auth for the owned local HTTP stub only
URL = "http://assembled-hermes:8700/v1"
ANSWER = "Synthetic assembled reply."


class ObservedHermesClient(HermesClient):
    def _request(self, *args, **kwargs):
        response = super()._request(*args, **kwargs)
        if args[0] == "POST" and args[1].endswith("/runs"):
            self.contract_headers.append(response.headers.get("X-Bumpa-Context-Contract"))
        return response


def main():
    deadline = time.monotonic() + 35
    while True:
        try:
            if httpx.get("http://assembled-hermes:8700/health", headers={"Authorization": "Bearer " + KEY}, timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        assert time.monotonic() < deadline, "actual Hermes gateway never became reachable"
        time.sleep(0.2)
    settings = Settings(app_env="test", hermes_base_internal_host="http://assembled-hermes", hermes_profile_port_start=8700, hermes_profile_port_end=8700, hermes_read_timeout_seconds=90, agent_backend="hermes")
    client = ObservedHermesClient(settings)
    client.contract_headers = []
    endpoint = HermesEndpoint(profile_name="canary", api_url=URL, api_key=KEY)
    prepared = []
    admitted = []
    for index, history in enumerate([
        [{"role": "user", "content": "ASSEMBLED_CANONICAL_VISIBLE_FACT"}, {"role": "assistant", "content": "A visible synthetic prior reply."}],
        [],
    ]):
        message = "Please repeat the synthetic response." if index == 0 else "Please repeat it again."
        result = client.respond(endpoint, message=message, business_context="Synthetic current application facts.", history=history, content_parts=[{"type": "text", "text": message}] if index == 0 else None, session_id="SYNTHETIC_NATIVE_ID_MUST_NOT_BE_SENT", session_key="bb_native_key_must_not_be_sent", request_id=f"assembled-case-{index}", on_prepared=prepared.append, on_admitted=admitted.append)
        assert result.content == ANSWER
        assert result.session_key is None
    assert client.contract_headers == ["1", "1"]
    assert len(admitted) == len(prepared) == 2
    assert all("session_id" not in row["payload"] and "X-Hermes-Session-Key" not in row["headers"] for row in prepared)
    print("ASSEMBLED_CLIENT_RESULT=" + json.dumps({"passed": True, "completed_real_client_turns": 2, "context_contract_headers": client.contract_headers, "prepared_payloads": prepared, "admitted_run_ids": admitted, "actual_hermes_client_sha256": hashlib.sha256(Path("/app/app/providers/hermes.py").read_bytes()).hexdigest(), "transport": "real httpx over owned internal Docker bridge", "application_database_used": False, "cloud_provider_used": False}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
