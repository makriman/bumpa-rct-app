"""Exercise real pinned Hermes modules and HTTP gateway against a loopback fake model.

The runner uses Docker --network none. All data, API keys, notices, and responses
are synthetic; the image contains this harness and no production configuration.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch


NOTICE = "SYNTHETIC_INTERNAL_NOTICE_7e39"
STALE = "SYNTHETIC_STALE_NATIVE_HISTORY_8b62"
SIDECAR = "SYNTHETIC_OLD_INJECTED_SIDECAR_9c71"
MEMORY = "SYNTHETIC_UNMANAGED_MEMORY_FILE_2d48"
CANONICAL = "SYNTHETIC_CANONICAL_MERCHANT_FACT_3f52"
RETRIEVED = "SYNTHETIC_RETRIEVED_UNTRUSTED_EVIDENCE_6a04"
PLUGIN = "SYNTHETIC_PLUGIN_UNTRUSTED_EVIDENCE_0c84"
ANSWER = "Synthetic local canary reply."
KEY = "dca217b5449f834bd6081c653be9c0b56970a4a2659d01c572ff22c8fb2be380a"  # gitleaks:allow -- reviewed synthetic auth for the owned local HTTP stub only
MODEL = "synthetic-hermes-canary"


def digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_users(wire: list[dict], expected: list[str]) -> None:
    def text(content):
        if isinstance(content, list):
            return " ".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
        return content

    users = [text(message["content"]) for message in wire if message.get("role") == "user"]
    assert users == expected, {"expected_users": expected, "actual_users": users}
    for message in wire:
        if message.get("role") in {"user", "assistant"}:
            assert NOTICE not in str(message), message
            assert SIDECAR not in str(message), message


def verify_real_turn_functions(base_url: str) -> dict:
    from agent import turn_context
    from run_agent import AIAgent

    agent = AIAgent(
        model=MODEL, provider="custom", base_url=base_url,
        api_key="synthetic-loopback-model-key", api_mode="chat_completions",
        enabled_toolsets=[], disabled_toolsets=["memory", "session_search"],
        max_iterations=2, quiet_mode=True, skip_memory=True,
        skip_background_review=True, skip_context_files=True,
        ephemeral_system_prompt="Synthetic current app facts.",
    )
    try:
        # build_api_messages normally receives this admission-time stamp from
        # the real turn prologue; supply it for the direct-function check.
        agent._current_turn_timestamp = time.time()
        messages = [
            {"role": "user", "content": CANONICAL, "api_content": SIDECAR},
            {"role": "assistant", "content": "A visible synthetic prior reply."},
            {"role": "user", "content": "A visible synthetic current question."},
        ]
        agent._gateway_turn_context_notes = NOTICE
        plugin = turn_context._merge_gateway_notes(agent, messages, 2, PLUGIN)
        turn_context._stamp_api_content_sidecar(
            agent, messages, 2, RETRIEVED, plugin, preflight_compressed=False,
        )
        assert "api_content" not in messages[2], messages[2]
        wire, system = turn_context.build_api_messages(
            agent, messages, current_turn_user_idx=2,
            ext_prefetch_cache=RETRIEVED, plugin_user_context=plugin,
            moa_config=None, active_system_prompt="Synthetic policy.",
        )
        assert_users(wire, [CANONICAL, "A visible synthetic current question."])
        assert NOTICE in system and RETRIEVED in system and PLUGIN in system, system
        assert "never shown to the merchant" in system
        assert "untrusted evidence" in system
        assert SIDECAR not in str(wire)
        # The list-input path used by the application must not append runtime notes
        # into the merchant's text blocks or replay them as merchant speech.
        parts = [{"type": "text", "text": "Synthetic multipart merchant question."}]
        list_messages = [{"role": "user", "content": copy.deepcopy(parts)}]
        agent._gateway_turn_context_notes = NOTICE
        plugin = turn_context._merge_gateway_notes(agent, list_messages, 0, PLUGIN)
        turn_context._stamp_api_content_sidecar(
            agent, list_messages, 0, RETRIEVED, plugin, preflight_compressed=False,
        )
        turn_context._append_multimodal_context(
            agent, list_messages[0], RETRIEVED, plugin, preflight_compressed=False,
        )
        assert list_messages[0]["content"] == parts, list_messages
        wire, system = turn_context.build_api_messages(
            agent, list_messages, current_turn_user_idx=0,
            ext_prefetch_cache=RETRIEVED, plugin_user_context=plugin,
            moa_config=None, active_system_prompt="Synthetic policy.",
        )
        assert NOTICE not in str([row for row in wire if row["role"] == "user"])
        assert RETRIEVED not in str([row for row in wire if row["role"] == "user"])
        assert PLUGIN not in str([row for row in wire if row["role"] == "user"])
        return {"real_imports": True, "string_sidecar_replay": "passed", "multipart_notes": "passed"}
    finally:
        close = getattr(agent, "close", None)
        if close:
            close()


async def verify_native_autonomy_gate(home: Path) -> dict:
    from cron.jobs import create_job, save_jobs, use_cron_store
    from cron import scheduler_provider
    from gateway import run as gateway_run
    from gateway import run_bootstrap

    profiles = [home / "profiles" / f"synthetic-business-{index}" for index in range(5)]
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(second=0, microsecond=0)
    for profile in profiles:
        profile.mkdir(parents=True)
        with use_cron_store(profile):
            job = create_job(
                prompt="SYNTHETIC_PRESERVED_NATIVE_JOB_MUST_NEVER_EXECUTE",
                schedule="* * * * *", name="Synthetic old native evidence",
                model=MODEL, provider="custom", deliver="local",
            )
            job["next_run_at"] = due.isoformat()
            save_jobs([job], replace=True)
    files = [path for profile in profiles for path in profile.rglob("*") if path.is_file()]
    before = {str(path): digest(path) for path in files}
    heartbeat = threading.Event()
    autonomous_calls = []

    def forbidden(*args, **kwargs):
        autonomous_calls.append("forbidden-native-autonomy")
        raise AssertionError("Native cron/delivery/housekeeping must never execute")

    with ExitStack() as guards:
        guards.enter_context(patch.object(scheduler_provider, "resolve_cron_scheduler", forbidden))
        for name in ["_start_gateway_housekeeping", "_drain_restart_safe_cron_deliveries", "_housekeeping_misfire_catch_up"]:
            guards.enter_context(patch.object(gateway_run, name, forbidden))
        guards.enter_context(patch.object(gateway_run, "_write_runtime_status_quiet", heartbeat.set))
        stop, provider, cron_thread, status_thread = run_bootstrap._start_gateway_start_cron_and_housekeeping(
            SimpleNamespace(adapters={}, config=SimpleNamespace(multiplex_profiles=True))
        )
        try:
            assert heartbeat.wait(timeout=1), "heartbeat-only thread failed to publish"
            assert provider is None and cron_thread is None
            assert status_thread.name == "bumpa-runtime-heartbeat"
            assert status_thread.is_alive()
            assert not autonomous_calls
        finally:
            stop.set()
            assert await gateway_run._await_thread_exit(status_thread, timeout=1)
            assert await gateway_run._await_thread_exit(cron_thread, timeout=1)
            gateway_run._stop_cron_provider(provider)
    after = {str(path): digest(path) for profile in profiles for path in profile.rglob("*") if path.is_file()}
    assert before == after, "native cron evidence changed or new native output appeared"
    assert not status_thread.is_alive()
    return {
        "passed": True, "synthetic_profile_count": len(profiles),
        "valid_due_jobs_preserved": len(profiles), "native_evidence_sha256": before,
        "scheduler_provider_started": False, "cron_thread_started": False,
        "delivery_or_autonomous_housekeeping_calls": len(autonomous_calls),
        "heartbeat_observed": True, "clean_shutdown": True,
    }


async def main() -> None:
    from aiohttp import ClientSession, ClientTimeout, web

    started = time.monotonic()
    home = Path(tempfile.mkdtemp(prefix="hermes-context-canary-"))
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HOME"] = str(home)
    os.environ["OPENAI_API_KEY"] = "synthetic-loopback-model-key"
    os.environ["PYTHONUNBUFFERED"] = "1"
    captures: list[dict] = []

    async def fake_model(request: web.Request) -> web.StreamResponse:
        assert request.remote in {"127.0.0.1", "::1"}, request.remote
        body = await request.json()
        captures.append(body)
        assert body.get("model") == MODEL, body.get("model")
        response_id = f"chatcmpl-synthetic-{len(captures)}"
        common = {"id": response_id, "created": int(time.time()), "model": MODEL}
        if body.get("stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for delta, finish in [({"role": "assistant", "content": ANSWER}, None), ({}, "stop")]:
                chunk = {**common, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                await response.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return web.json_response({
            **common, "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
        })

    fake_app = web.Application()
    fake_app.router.add_post("/v1/chat/completions", fake_model)
    fake_runner = web.AppRunner(fake_app)
    await fake_runner.setup()
    fake_site = web.TCPSite(fake_runner, "127.0.0.1", 0)
    await fake_site.start()
    fake_port = fake_site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{fake_port}/v1"
    os.environ["OPENAI_BASE_URL"] = base_url
    config = {
        "model": {"default": MODEL, "provider": "custom", "base_url": base_url, "api_key": "synthetic-loopback-model-key", "api_mode": "chat_completions"},
        "agent": {"max_turns": 2},
        # Deliberately request the native memory toolset: the production overlay
        # must disable it even if an imported profile had enabled it before.
        "platform_toolsets": {"api_server": ["memory"]},
        "auxiliary": {"title_generation": {"enabled": False, "model_upgrade_enabled": False}},
        "memory": {"memory_enabled": True, "user_profile_enabled": True},
        "background_review": {"enabled": False},
        "gateway": {"api_server": {"max_concurrent_runs": 2}},
    }
    (home / "config.yaml").write_text(json.dumps(config))
    (home / "memories").mkdir()
    (home / "memories" / "MEMORY.md").write_text(MEMORY)
    (home / "memories" / "USER.md").write_text(MEMORY)

    result = {
        "base_image": "nousresearch/hermes-agent@sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb",
        "overlay_sha256": digest("/canary/apply_context_overlay.py"),
        "harness_sha256": digest(__file__),
        "actual_patched_source_sha256": {
            path: digest(path) for path in [
                "/opt/hermes/agent/turn_context.py",
                "/opt/hermes/gateway/platforms/api_server_runs.py",
                "/opt/hermes/gateway/platforms/api_server.py",
                "/opt/hermes/agent/system_prompt.py",
                "/opt/hermes/agent/agent_init.py",
                "/opt/hermes/gateway/run_bootstrap.py",
            ]
        },
        "isolation": {"model_endpoint": "loopback-only synthetic", "external_network": "runner requires --network none", "production_mounts": False},
    }
    adapter = None
    try:
        if os.getenv("CANARY_EXPECT_PRODUCTION_BASELINE") == "1":
            import importlib.metadata

            versions = {name: importlib.metadata.version(name) for name in ["hermes-agent", "brotlicffi"]}
            assert versions == {"hermes-agent": "0.22.5", "brotlicffi": "1.2.1.0"}, versions
            result["production_baseline_versions"] = versions
        result["native_autonomy_gate"] = await verify_native_autonomy_gate(home)
        print("CANARY_PROGRESS=native_autonomy_disabled_evidence_preserved", flush=True)
        result["real_turn_functions"] = verify_real_turn_functions(base_url)
        print("CANARY_PROGRESS=real_patched_turn_functions_passed", flush=True)
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter

        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
            "host": "127.0.0.1", "port": 8642, "key": KEY,
            "model_name": "canary", "model_routes": {"canary": {
                "model": MODEL, "provider": "custom", "base_url": base_url,
                "api_key": "synthetic-loopback-model-key",
            }},
        }))
        # Add one synthetic runtime notice while retaining the real constructor,
        # real admission/executor, real conversation loop, and real wire adapter.
        create_agent = adapter._create_agent
        agents = []

        def with_notice(*args, **kwargs):
            agent = create_agent(*args, **kwargs)
            agent._gateway_turn_context_notes = NOTICE
            agents.append(agent)
            return agent

        adapter._create_agent = with_notice
        assert await adapter.connect(), "real API server adapter failed to start"
        timeout = ClientTimeout(total=90)
        async with ClientSession(headers={"Authorization": "Bearer " + KEY}, timeout=timeout) as client:
            async def run(body: dict, token: str) -> tuple[dict, dict, dict]:
                before = len(captures)
                async with client.post("http://127.0.0.1:8642/v1/runs", json=body, headers={"Idempotency-Key": token}) as response:
                    admitted = await response.json()
                    assert response.status == 202, (response.status, admitted)
                    assert response.headers.get("X-Bumpa-Context-Contract") == "1", dict(response.headers)
                run_id = admitted["run_id"]
                deadline = time.monotonic() + 75
                while time.monotonic() < deadline:
                    async with client.get(f"http://127.0.0.1:8642/v1/runs/{run_id}") as response:
                        final = await response.json()
                        assert response.status == 200, (response.status, final)
                    if final["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                        break
                    await asyncio.sleep(0.05)
                assert final["status"] == "completed", final
                assert final.get("output") == ANSWER, final
                assert len(captures) == before + 1, {"before": before, "after": len(captures)}
                wire = captures[-1]["messages"]
                assert MEMORY not in str(wire), "unmanaged native memory reached model"
                assert NOTICE not in str([row for row in wire if row.get("role") in {"user", "assistant"}]), wire
                assert NOTICE in str([row for row in wire if row.get("role") == "system"]), "synthetic internal notice absent from typed system context"
                print("CANARY_PROGRESS=" + token + "_completed", flush=True)
                return admitted, final, copy.deepcopy(captures[-1])

            native_body = {"model": "canary", "input": STALE, "instructions": "Synthetic canary policy.", "conversation_history": [], "session_id": "synthetic-affinity-session"}
            await run(native_body, "synthetic-seed-stale")
            canonical_body = {
                "model": "canary", "input": [{"role": "user", "content": [{"type": "text", "text": "Synthetic canonical current question."}]}],
                "instructions": "Synthetic current application context.",
                "conversation_history": [{"role": "user", "content": CANONICAL}, {"role": "assistant", "content": "A visible canonical reply."}],
            }
            admitted, final, capture = await run(canonical_body, "synthetic-canonical-override")
            assert STALE not in str(capture["messages"]), "stale native DB history entered fresh canonical run"
            assert CANONICAL in str(capture["messages"])
            assert_users(capture["messages"], [CANONICAL, "Synthetic canonical current question."])
            model_calls = len(captures)
            async with client.post("http://127.0.0.1:8642/v1/runs", json=canonical_body, headers={"Idempotency-Key": "synthetic-canonical-override"}) as response:
                replay = await response.json()
                assert response.status == 202, (response.status, replay)
                assert response.headers.get("X-Bumpa-Context-Contract") == "1"
                assert response.headers.get("Idempotency-Replayed") == "true"
                assert replay["run_id"] == admitted["run_id"]
            assert len(captures) == model_calls, "idempotency replay repeated model execution"
            # Mirrors the app's empty-canonical request: no native affinity is sent.
            _, _, empty_capture = await run({
                "model": "canary", "input": [{"role": "user", "content": "Synthetic empty-context current question."}],
                "instructions": "Synthetic current application context.", "conversation_history": [],
            }, "synthetic-empty-canonical")
            assert STALE not in str(empty_capture["messages"])
            assert CANONICAL not in str(empty_capture["messages"])
            assert_users(empty_capture["messages"], ["Synthetic empty-context current question."])
            result["gateway"] = {
                "real_authenticated_post_and_poll": "passed",
                "fresh_canonical_run_excludes_stale_native_history": "passed",
                "empty_context_without_affinity": "passed",
                "multipart_input_is_visible_merchant_text_only": "passed",
                "internal_notice_typed_system_only": "passed",
                "context_contract_admission_and_replay": "1",
                "idempotent_replay_no_duplicate_model_call": "passed",
                "model_calls": len(captures),
                "agent_instances": len(agents),
            }
            if os.getenv("CANARY_EXPECT_NATIVE_DISABLED") == "1":
                for agent in agents:
                    assert agent._memory_manager is None
                    assert agent._memory_store is None
                    assert not agent._memory_enabled
                    assert not agent._user_profile_enabled
                    assert agent.skip_background_review
                    # Hermes retains the original request on enabled_toolsets;
                    # _load_tools filters the effective construction arguments.
                    assert not ({"memory", "session_search", "cronjob", "kanban", "delegate_task"} & set(agent.valid_tool_names)), agent.valid_tool_names
                    assert not any(
                        tool.get("function", {}).get("name") in {"memory", "session_search"}
                        for tool in (agent.tools or []) if isinstance(tool, dict)
                    ), agent.tools
                result["gateway"]["unmanaged_native_memory_disabled"] = "passed"
                result["gateway"]["effective_tool_names"] = [sorted(agent.valid_tool_names) for agent in agents]
        result["passed"] = True
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        print("CANARY_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
    finally:
        if adapter is not None:
            await adapter.disconnect()
        await fake_runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
