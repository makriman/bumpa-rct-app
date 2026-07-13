from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path(__file__).parents[3] / "infra/hermes/control_server.py"
    spec = importlib.util.spec_from_file_location("bumpabestie_hermes_control", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_staged_profile(
    root: Path,
    *,
    name: str = "tenant_safe",
    key: str = "private-profile-key",
    port: int = 8799,
) -> Path:
    profile = root / name
    profile.mkdir(mode=0o2750)
    profile.chmod(0o2750)
    for child in ("skills", "memories", "sessions", "cron"):
        path = profile / child
        path.mkdir(mode=0o750)
        path.chmod(0o750)
    files = {
        ".no-skills": "",
        ".env": (
            "API_SERVER_ENABLED=true\n"
            "API_SERVER_HOST=0.0.0.0\n"
            f"API_SERVER_PORT={port}\n"
            f"API_SERVER_KEY={key}\n"
        ),
        "config.yaml": "model:\n  provider: anthropic\n",
        "SOUL.md": "Strictly isolated synthetic test profile.\n",
    }
    for filename, content in files.items():
        path = profile / filename
        path.write_text(content, encoding="utf-8")
        path.chmod(0o640)
    return profile


def test_profile_authentication_is_exact_and_symlinks_are_refused(tmp_path: Path) -> None:
    control = _module()
    profile = tmp_path / "tenant_safe"
    profile.mkdir()
    (profile / ".env").write_text(
        "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=private-profile-key\nAPI_SERVER_PORT=8799\n",
        encoding="utf-8",
    )

    resolved = control._profile_directory(tmp_path, "tenant_safe")
    assert resolved == profile.resolve()
    assert control._profile_key(resolved) == "private-profile-key"
    assert control._authorised("Bearer private-profile-key", "private-profile-key") is True
    assert control._authorised("Bearer wrong-profile-key", "private-profile-key") is False
    assert control._authorised(None, "private-profile-key") is False

    (tmp_path / "tenant_linked").symlink_to(profile, target_is_directory=True)
    with pytest.raises(control.ControlError) as linked:
        control._profile_directory(tmp_path, "tenant_linked")
    assert linked.value.status == control.HTTPStatus.NOT_FOUND
    with pytest.raises(control.ControlError):
        control._profile_directory(tmp_path, "../../etc")


def test_lifecycle_invokes_only_native_profile_gateway_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _module()
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        captured.update({"command": command, **kwargs})
        return Completed()

    monkeypatch.setattr(control.shutil, "which", lambda name: "/usr/local/bin/hermes")
    monkeypatch.setattr(control.subprocess, "run", fake_run)
    control.restart_profile("tenant_safe")

    assert captured["command"] == [
        "/usr/local/bin/hermes",
        "-p",
        "tenant_safe",
        "gateway",
        "restart",
    ]
    assert captured["check"] is False
    assert captured["timeout"] == 8
    assert captured["close_fds"] is True
    assert captured["cwd"] == "/opt/hermes"
    assert captured["stdin"] == control.subprocess.DEVNULL
    assert captured["stdout"] == control.subprocess.DEVNULL
    assert captured["stderr"] == control.subprocess.DEVNULL
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["HERMES_HOME"] == "/opt/data"
    assert environment["HERMES_WRITE_SAFE_ROOT"] == "/opt/data"
    assert "ANTHROPIC_API_KEY" not in environment

    control.start_profile("tenant_safe")
    assert captured["command"] == [
        "/usr/local/bin/hermes",
        "-p",
        "tenant_safe",
        "gateway",
        "start",
    ]


def test_restart_failure_never_returns_command_output(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _module()

    class Failed:
        returncode = 9

    monkeypatch.setattr(control.shutil, "which", lambda name: "/usr/local/bin/hermes")
    monkeypatch.setattr(control.subprocess, "run", lambda *args, **kwargs: Failed())
    with pytest.raises(control.ControlError) as failure:
        control.restart_profile("tenant_safe")
    assert failure.value.status == control.HTTPStatus.SERVICE_UNAVAILABLE
    assert str(failure.value) == "Hermes restart failed"


def test_staged_profile_is_allowlisted_and_atomically_imported(tmp_path: Path) -> None:
    control = _module()
    staging_root = tmp_path / "staging"
    runtime_root = tmp_path / "runtime"
    staging_root.mkdir()
    runtime_root.mkdir()
    _write_staged_profile(staging_root)

    staged = control._read_staged_profile(staging_root, "tenant_safe")
    assert staged.api_key == "private-profile-key"
    assert staged.api_port == 8799
    activated = control.activate_profile(
        staging_root,
        runtime_root,
        "tenant_safe",
        staged,
    )

    assert activated == runtime_root / "tenant_safe"
    assert set(item.name for item in activated.iterdir()) == control.PROFILE_ENTRIES
    assert stat.S_IMODE(activated.stat().st_mode) == 0o700
    assert stat.S_IMODE((activated / ".env").stat().st_mode) == 0o600
    assert control._profile_runtime(activated) == ("private-profile-key", 8799)
    assert control.activate_profile(staging_root, runtime_root, "tenant_safe", staged) == activated


@pytest.mark.parametrize(
    ("key", "port"),
    (("different-profile-key", 8799), ("private-profile-key", 8800)),
)
def test_existing_runtime_must_match_staged_key_and_port(
    tmp_path: Path,
    key: str,
    port: int,
) -> None:
    control = _module()
    staging_root = tmp_path / "staging"
    runtime_root = tmp_path / "runtime"
    staging_root.mkdir()
    runtime_root.mkdir()
    _write_staged_profile(staging_root)
    original = control._read_staged_profile(staging_root, "tenant_safe")
    control.activate_profile(staging_root, runtime_root, "tenant_safe", original)
    conflicting = control.StagedProfile(files=original.files, api_key=key, api_port=port)

    with pytest.raises(control.ControlError) as failure:
        control.activate_profile(staging_root, runtime_root, "tenant_safe", conflicting)

    assert failure.value.status == control.HTTPStatus.CONFLICT
    assert str(failure.value) == "Runtime profile conflicts with staging"
    assert control._profile_runtime(runtime_root / "tenant_safe") == (
        "private-profile-key",
        8799,
    )


def test_existing_runtime_must_match_every_policy_file(tmp_path: Path) -> None:
    control = _module()
    staging_root = tmp_path / "staging"
    runtime_root = tmp_path / "runtime"
    staging_root.mkdir()
    runtime_root.mkdir()
    _write_staged_profile(staging_root)
    staged = control._read_staged_profile(staging_root, "tenant_safe")
    runtime = control.activate_profile(staging_root, runtime_root, "tenant_safe", staged)
    (runtime / "config.yaml").write_text("model:\n  provider: changed\n", encoding="utf-8")
    (runtime / "config.yaml").chmod(0o600)

    with pytest.raises(control.ControlError) as failure:
        control.activate_profile(staging_root, runtime_root, "tenant_safe", staged)

    assert failure.value.status == control.HTTPStatus.CONFLICT
    assert str(failure.value) == "Runtime profile conflicts with staging"


def test_staged_environment_rejects_unknown_or_malformed_lines(tmp_path: Path) -> None:
    control = _module()
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    profile = _write_staged_profile(staging_root)
    environment = profile / ".env"
    environment.write_text(
        environment.read_text(encoding="utf-8") + "ANTHROPIC_API_KEY=must-never-be-imported\n",
        encoding="utf-8",
    )
    environment.chmod(0o640)

    with pytest.raises(control.ControlError) as failure:
        control._read_staged_profile(staging_root, "tenant_safe")

    assert failure.value.status == control.HTTPStatus.BAD_REQUEST
    assert str(failure.value) == "Staged profile is invalid"


@pytest.mark.parametrize("unsafe_kind", ("symlink", "fifo", "unexpected"))
def test_staged_profile_rejects_symlinks_special_files_and_unexpected_entries(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    control = _module()
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    profile = _write_staged_profile(staging_root)
    if unsafe_kind == "symlink":
        (profile / "config.yaml").unlink()
        (profile / "config.yaml").symlink_to("/etc/passwd")
    elif unsafe_kind == "fifo":
        (profile / ".env").unlink()
        os.mkfifo(profile / ".env", mode=0o640)
    else:
        (profile / "unexpected.txt").write_text("not allowlisted", encoding="utf-8")

    with pytest.raises(control.ControlError) as failure:
        control._read_staged_profile(staging_root, "tenant_safe")

    assert failure.value.status == control.HTTPStatus.NOT_FOUND
    assert str(failure.value) == "Profile not found"


def test_failed_atomic_import_removes_temporary_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _module()
    staging_root = tmp_path / "staging"
    runtime_root = tmp_path / "runtime"
    staging_root.mkdir()
    runtime_root.mkdir()
    _write_staged_profile(staging_root)
    staged = control._read_staged_profile(staging_root, "tenant_safe")

    def fail_rename(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic atomic rename failure")

    monkeypatch.setattr(control.os, "rename", fail_rename)
    with pytest.raises(control.ControlError) as failure:
        control.activate_profile(staging_root, runtime_root, "tenant_safe", staged)

    assert failure.value.status == control.HTTPStatus.SERVICE_UNAVAILABLE
    assert not (runtime_root / "tenant_safe").exists()
    assert list(runtime_root.iterdir()) == []
