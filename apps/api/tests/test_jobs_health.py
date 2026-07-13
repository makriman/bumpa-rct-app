from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import app.jobs
from app.jobs import health


class FakeRedis:
    def __init__(self, *, exists: object = 1, error: Exception | None = None) -> None:
        self.exists_result = exists
        self.error = error
        self.keys: list[str] = []
        self.closed = False

    def exists(self, key: str) -> object:
        self.keys.append(key)
        if self.error:
            raise self.error
        return self.exists_result

    def close(self) -> None:
        self.closed = True


def test_probe_is_bounded_and_checks_only_the_requested_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    client = FakeRedis()

    def from_url(url: str, **kwargs: object) -> FakeRedis:
        captured.update({"url": url, **kwargs})
        return client

    monkeypatch.setenv("ASYNC_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("REDIS_URL", "redis://:credential@redis:6379/0")
    monkeypatch.setenv("ASYNC_QUEUE_KEY_PREFIX", "tenant-safe")
    monkeypatch.setattr(health.Redis, "from_url", from_url)

    assert health._heartbeat_exists("worker") is True
    assert client.keys == ["tenant-safe:health:worker"]
    assert client.closed is True
    assert captured == {
        "url": "redis://:credential@redis:6379/0",
        "decode_responses": True,
        "socket_connect_timeout": 1,
        "socket_timeout": 1,
        "health_check_interval": 0,
        "retry_on_timeout": False,
    }


@pytest.mark.parametrize(
    ("environment", "error"),
    [
        ({"ASYNC_RUNTIME_ENABLED": "false"}, None),
        ({"ASYNC_RUNTIME_ENABLED": "invalid"}, None),
        (
            {"ASYNC_RUNTIME_ENABLED": "true", "ASYNC_QUEUE_KEY_PREFIX": "unsafe:prefix"},
            None,
        ),
        ({"ASYNC_RUNTIME_ENABLED": "true", "REDIS_URL": ""}, None),
        ({"ASYNC_RUNTIME_ENABLED": "true"}, RuntimeError("redis://:secret@redis")),
    ],
)
def test_probe_fails_closed_without_leaking_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    error: Exception | None,
) -> None:
    for name in ("ASYNC_RUNTIME_ENABLED", "ASYNC_QUEUE_KEY_PREFIX", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    client = FakeRedis(error=error)
    monkeypatch.setattr(health.Redis, "from_url", lambda *_args, **_kwargs: client)

    assert health._heartbeat_exists("scheduler") is False
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_health_module_import_does_not_load_application_runtime() -> None:
    api_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and static probe command
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.jobs.health; "
                "assert 'app.jobs.runtime' not in sys.modules; "
                "assert 'sqlalchemy' not in sys.modules"
            ),
        ],
        cwd=api_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == 0, result.stderr


def test_lazy_jobs_exports_preserve_the_public_runtime_api() -> None:
    from app.jobs.runtime import PermanentJobError, enqueue_job, register_handler

    assert app.jobs.PermanentJobError is PermanentJobError
    assert app.jobs.enqueue_job is enqueue_job
    assert app.jobs.register_handler is register_handler
