"""Actual Hermes gateway + synthetic loopback model for the two-image canary."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
import time

KEY = "dca217b5449f834bd6081c653be9c0b56970a4a2659d01c572ff22c8fb2be380a"  # gitleaks:allow -- reviewed synthetic auth for the owned local HTTP stub only
MODEL = "synthetic-hermes-canary"
NOTICE = "ASSEMBLED_SYNTHETIC_INTERNAL_NOTICE"
ANSWER = "Synthetic assembled reply."
EVIDENCE = Path("/tmp/assembled-server-evidence.json")


async def main():
    from aiohttp import web

    home = Path(tempfile.mkdtemp(prefix="assembled-hermes-"))
    os.environ.update(HERMES_HOME=str(home), HOME=str(home), OPENAI_API_KEY="synthetic-loopback-key", OPENAI_BASE_URL="http://127.0.0.1:9876/v1")
    (home / "config.yaml").write_text(json.dumps({
        "model": {"default": MODEL, "provider": "custom", "base_url": "http://127.0.0.1:9876/v1", "api_key": "synthetic-loopback-key", "api_mode": "chat_completions"},
        "platform_toolsets": {"api_server": []}, "agent": {"max_turns": 2},
        "auxiliary": {"title_generation": {"enabled": False, "model_upgrade_enabled": False}},
        "gateway": {"api_server": {"max_concurrent_runs": 1}},
    }))
    evidence = {"model_requests": [], "admissions": []}

    def save():
        EVIDENCE.write_text(json.dumps(evidence, sort_keys=True))

    async def fake_model(request):
        assert request.remote == "127.0.0.1"
        body = await request.json()
        assert body["model"] == MODEL
        evidence["model_requests"].append(body)
        save()
        common = {"id": "chatcmpl-synthetic-assembled", "created": int(time.time()), "model": MODEL}
        if body.get("stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for delta, finish in [({"role": "assistant", "content": ANSWER}, None), ({}, "stop")]:
                chunk = {**common, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                await response.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return web.json_response({**common, "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    fake = web.Application()
    fake.router.add_post("/v1/chat/completions", fake_model)
    fake.router.add_get("/v1/models", lambda request: web.json_response({"object": "list", "data": [{"id": MODEL, "object": "model", "context_length": 32000}]}))
    fake_runner = web.AppRunner(fake)
    await fake_runner.setup()
    await web.TCPSite(fake_runner, "127.0.0.1", 9876).start()
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"host": "0.0.0.0", "port": 8700, "key": KEY, "model_name": "canary", "model_routes": {"canary": {"model": MODEL, "provider": "custom", "base_url": "http://127.0.0.1:9876/v1", "api_key": "synthetic-loopback-key"}}}))
    create_agent = adapter._create_agent

    def with_notice(*args, **kwargs):
        agent = create_agent(*args, **kwargs)
        agent._gateway_turn_context_notes = NOTICE
        return agent

    adapter._create_agent = with_notice
    handle_runs = adapter._handle_runs

    async def record_admission(request):
        entry = {"payload": await request.json(), "native_affinity_header": request.headers.get("X-Hermes-Session-Key")}
        response = await handle_runs(request)
        entry.update(status=response.status, context_contract=response.headers.get("X-Bumpa-Context-Contract"))
        evidence["admissions"].append(entry)
        save()
        return response

    adapter._handle_runs = record_admission
    assert await adapter.connect()
    save()
    print("ASSEMBLED_HERMES_READY", flush=True)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await asyncio.wait_for(stop.wait(), timeout=240)
    except TimeoutError:
        pass
    finally:
        save()
        await adapter.disconnect()
        await fake_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
