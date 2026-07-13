from __future__ import annotations

import hashlib
import hmac
import importlib.util
import io
import json
import stat
from pathlib import Path
from urllib.error import HTTPError

SCRIPT = Path(__file__).parents[1] / "send_ops_alert.py"
SPEC = importlib.util.spec_from_file_location("send_ops_alert", SCRIPT)
assert SPEC and SPEC.loader
alerts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alerts)


def test_disk_event_is_bounded_and_drops_paths_host_and_device_ids() -> None:
    envelope = alerts.sanitize_event(
        "disk_usage",
        {
            "checked_at": "2026-07-13T10:00:00Z",
            "status": "alert",
            "host": "private-hostname",
            "threshold_percent": 85,
            "near_full_device_ids": [12345],
            "errors": [],
            "filesystems": [
                {
                    "aliases": ["/private/path"],
                    "device_id": 12345,
                    "blocks": {"used_percent": 91},
                    "inodes": {"used_percent": 72},
                }
            ],
        },
    )
    serialized = json.dumps(envelope)
    assert envelope["event_type"] == "disk_capacity_failure"
    assert envelope["attributes"]["block_used_percent"] == 91
    assert "private-hostname" not in serialized
    assert "/private/path" not in serialized
    assert "12345" not in serialized
    assert len(serialized) < alerts.MAX_OUTPUT_BYTES


def test_backup_success_has_deterministic_id_and_no_application_environment() -> None:
    source = {
        "event": "backup",
        "occurred_at": "2026-07-13T10:00:00Z",
        "status": "success",
        "DATABASE_URL": "private",
    }
    first = alerts.sanitize_event("backup", source)
    second = alerts.sanitize_event("backup", source)
    assert first == second
    assert first["event_type"] == "backup_success"
    assert "DATABASE_URL" not in json.dumps(first)


def test_signed_request_uses_hmac_and_idempotency_header() -> None:
    envelope = alerts.sanitize_event(
        "backup",
        {"occurred_at": "2026-07-13T10:00:00Z", "status": "failure"},
    )
    secret = "s" * 40
    request = alerts.signed_request(
        "https://alerts.example.test/v1/events", secret, envelope
    )
    body = request.data
    assert body is not None
    timestamp = request.headers["X-bumpabestie-timestamp"]
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    assert request.headers["X-bumpabestie-signature"] == f"v1={expected}"
    assert request.headers["Idempotency-key"] == envelope["event_id"]


def test_secret_file_requires_private_regular_file(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("x" * 40, encoding="utf-8")
    secret.chmod(0o644)
    try:
        alerts.load_secret(str(secret))
    except ValueError as exc:
        assert "permissions" in str(exc)
    else:
        raise AssertionError("world-readable secret was accepted")
    secret.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert alerts.load_secret(str(secret)) == "x" * 40


def test_fixed_config_is_bounded_allowlisted_and_optional(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert alerts.load_fixed_config(str(missing)) == {}
    config = tmp_path / "alerts.json"
    config.write_text(
        json.dumps(
            {
                "webhook_url": "https://alerts.example.test/v1/events",
                "hmac_secret_file": "/private/alert-secret",
                "max_attempts": 3,
                "timeout_seconds": 10,
            }
        ),
        encoding="utf-8",
    )
    assert alerts.load_fixed_config(str(config))["max_attempts"] == 3
    config.write_text('{"unexpected":"private"}', encoding="utf-8")
    try:
        alerts.load_fixed_config(str(config))
    except ValueError as exc:
        assert "unknown fields" in str(exc)
    else:
        raise AssertionError("unknown alert config was accepted")


def test_sender_retries_retryable_status_with_one_idempotent_request(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    def opener(_request, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls < 3:
            raise HTTPError(
                "https://alerts.example.test", 503, "unavailable", {}, io.BytesIO(b"")
            )

        class Response:
            status = 204

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *_args):  # type: ignore[no-untyped-def]
                return None

            def read(self, _limit):  # type: ignore[no-untyped-def]
                return b""

        return Response()

    monkeypatch.setattr(alerts.time, "sleep", lambda _seconds: None)
    envelope = alerts.sanitize_event(
        "backup",
        {"occurred_at": "2026-07-13T10:00:00Z", "status": "failure"},
    )
    request = alerts.signed_request(
        "https://alerts.example.test/v1/events", "z" * 40, envelope
    )
    assert alerts.send_with_retries(
        request, attempts=3, timeout_seconds=1, opener=opener
    )
    assert calls == 3
