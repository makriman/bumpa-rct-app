from __future__ import annotations

import ast
import copy
import importlib.util
import logging
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parents[3]


def overlay():
    spec = importlib.util.spec_from_file_location(
        "context_overlay", ROOT / "infra/hermes/apply_context_overlay.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pinned_overlay_keeps_runtime_metadata_out_of_current_and_replayed_user_claims() -> None:
    source = (ROOT / "tests/fixtures/hermes_turn_context_bf0203a.py").read_text()
    patched = overlay().patch_source(source)
    names = {"_merge_gateway_notes", "_stamp_api_content_sidecar", "build_api_messages"}
    functions = [
        node
        for node in ast.parse(patched).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    ns = {
        "forward_lsp_verdicts": lambda _: None,
        "consume_gateway_turn_context_notes": lambda _: "Internal gateway notice",
        "pop_plugin_notices": lambda _: "",
        "consume_surface_switch_note": lambda _: "",
        "consume_skills_index_note": lambda _: (
            "Internal skill catalogue changed: synthetic-private-tool"
        ),
        "PERSISTENCE_ONLY_MESSAGE_FIELDS": set(),
    }
    exec(  # noqa: S102 -- executes selected functions from the hash-pinned local fixture
        "from __future__ import annotations\n" + "\n".join(ast.unparse(node) for node in functions),
        ns,
    )
    agent = SimpleNamespace(
        _current_turn_timestamp=1,
        ephemeral_system_prompt="Current app facts",
        _copy_reasoning_content_for_api=lambda *args: None,
        _should_sanitize_tool_calls=lambda: False,
    )
    messages = [
        {
            "role": "user",
            "content": "I personally mentioned synthetic-private-tool",
            "api_content": "Old injected internal notice",
        },
        {"role": "user", "content": "What does this mean?"},
    ]
    plugin = ns["_merge_gateway_notes"](agent, messages, 1, "untrusted plugin evidence")
    ns["_stamp_api_content_sidecar"](
        agent, messages, 1, "retrieved memory", plugin, preflight_compressed=False
    )
    assert "api_content" not in messages[1]
    modules = {}
    for name, members in {
        "agent.agent_runtime_helpers": {
            "fill_empty_non_final_wire_payload": lambda *args, **kwargs: None
        },
        "agent.conversation_loop": {"_clone_message_for_send": copy.deepcopy},
        "agent.replay_cleanup": {"canonicalize_replay_history": lambda rows, **kwargs: rows},
        "agent.image_replay": {"replay_for_agent": lambda *args: None},
        "agent.anthropic_thinking_replay": {
            "apply_rejected_thinking_suppression": lambda *args: None
        },
    }.items():
        module = ModuleType(name)
        module.__dict__.update(members)
        modules[name] = module
    with patch.dict("sys.modules", modules):
        wire, system = ns["build_api_messages"](
            agent,
            messages,
            current_turn_user_idx=1,
            ext_prefetch_cache="retrieved memory",
            plugin_user_context=plugin,
            moa_config=None,
            active_system_prompt="SME policy",
        )
    assert [row["content"] for row in wire if row["role"] == "user"] == [
        row["content"] for row in messages
    ]
    assert "Old injected internal notice" not in str(wire)
    assert "Internal skill catalogue changed" in system
    assert "never shown to the merchant" in system
    assert "untrusted evidence" in system


def test_overlay_refuses_unreviewed_upstream_source() -> None:
    with pytest.raises(RuntimeError, match="differs"):
        overlay().patch_source("unreviewed upstream")


def test_reviewed_run_admission_advertises_matching_context_contract() -> None:
    source = (ROOT / "tests/fixtures/hermes_api_server_runs_bf0203a.py").read_text()
    patched = overlay().patch_runs_source(source)
    assert 'headers["X-Bumpa-Context-Contract"] = "1"' in patched
    with pytest.raises(RuntimeError, match="differs"):
        overlay().patch_runs_source(source + "\n")


def test_reviewed_bootstrap_keeps_only_a_stoppable_status_heartbeat() -> None:
    source = (ROOT / "tests/fixtures/hermes_run_bootstrap_bf0203a.py").read_text()
    patched = overlay().patch_bootstrap_source(source)
    function = next(
        node
        for node in ast.parse(patched).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_start_gateway_start_cron_and_housekeeping"
    )
    heartbeat = threading.Event()
    started = []

    def start_writer(thread: threading.Thread) -> None:
        started.append(thread)
        thread.start()

    run = ModuleType("gateway.run")
    run._write_runtime_status_quiet = heartbeat.set
    ownership = ModuleType("gateway.runtime_ownership")
    ownership.process_ownership = SimpleNamespace(start_writer=start_writer)
    ns = {"threading": threading, "logger": logging.getLogger(__name__)}
    exec(  # noqa: S102 -- executes one function from the exact hash-pinned public fixture
        "from __future__ import annotations\n" + ast.unparse(function), ns
    )
    with patch.dict("sys.modules", {"gateway.run": run, "gateway.runtime_ownership": ownership}):
        stop, provider, cron_thread, status_thread = ns[
            "_start_gateway_start_cron_and_housekeeping"
        ](SimpleNamespace())
        try:
            assert heartbeat.wait(timeout=1)
            assert provider is None
            assert cron_thread is None
            assert started == [status_thread]
            assert status_thread.name == "bumpa-runtime-heartbeat"
        finally:
            stop.set()
            status_thread.join(timeout=1)
    assert not status_thread.is_alive()
    with pytest.raises(RuntimeError, match="differs"):
        overlay().patch_bootstrap_source(source + "\n")
