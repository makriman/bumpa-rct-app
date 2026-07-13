#!/usr/bin/env python3
"""Authenticated, profile-scoped Hermes lifecycle control plane.

The private service can activate one API-staged profile or restart one existing
runtime profile. Activation imports an allowlisted, read-only profile bundle into
Hermes' persistent data root before starting its gateway. It has no Docker socket,
host mount, shell execution, arbitrary command, or caller-supplied filesystem path.
"""

from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,159}$")
RESTART_PATH = re.compile(r"^/v1/profiles/([A-Za-z0-9][A-Za-z0-9_-]{1,159})/restart$")
ACTIVATE_PATH = re.compile(r"^/v1/profiles/([A-Za-z0-9][A-Za-z0-9_-]{1,159})/activate$")
MAX_BODY_BYTES = 256
MAX_PROFILE_FILE_BYTES = 131_072
MAX_PROFILE_BYTES = 262_144
PROFILE_FILES = frozenset({".no-skills", ".env", "config.yaml", "SOUL.md"})
PROFILE_DIRECTORIES = frozenset({"skills", "memories", "sessions", "cron"})
PROFILE_ENTRIES = PROFILE_FILES | PROFILE_DIRECTORIES
_ACTIVATION_LOCK = threading.Lock()


@dataclass(frozen=True)
class StagedProfile:
    files: dict[str, bytes]
    api_key: str
    api_port: int


class ControlError(RuntimeError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def _profile_directory(root: Path, profile_name: str) -> Path:
    if not PROFILE_NAME.fullmatch(profile_name):
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")
    try:
        root_real = root.resolve(strict=True)
        candidate = root_real / profile_name
        info = candidate.lstat()
        candidate_real = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")
    if candidate_real.parent != root_real:
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")
    return candidate_real


def _profile_runtime(profile_directory: Path) -> tuple[str, int]:
    env_path = profile_directory / ".env"
    try:
        info = env_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError("unsafe profile environment")
        content = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found") from exc
    values: dict[str, str] = {}
    for line in content.splitlines():
        name, separator, value = line.partition("=")
        if separator and name in {"API_SERVER_KEY", "API_SERVER_PORT"}:
            values[name] = value
    key = values.get("API_SERVER_KEY", "")
    try:
        port = int(values.get("API_SERVER_PORT", ""))
    except ValueError as exc:
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found") from exc
    if len(key) >= 8 and 1024 <= port <= 65535:
        return key, port
    raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")


def _runtime_values(content: bytes) -> tuple[str, int]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError(HTTPStatus.BAD_REQUEST, "Staged profile is invalid") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        name, separator, value = line.partition("=")
        allowed = {
            "API_SERVER_ENABLED",
            "API_SERVER_HOST",
            "API_SERVER_KEY",
            "API_SERVER_PORT",
        }
        if not separator or name not in allowed:
            raise ControlError(HTTPStatus.BAD_REQUEST, "Staged profile is invalid")
        if name in values:
            raise ControlError(HTTPStatus.BAD_REQUEST, "Staged profile is invalid")
        values[name] = value
    try:
        port = int(values.get("API_SERVER_PORT", ""))
    except ValueError as exc:
        raise ControlError(HTTPStatus.BAD_REQUEST, "Staged profile is invalid") from exc
    key = values.get("API_SERVER_KEY", "")
    if (
        set(values) != allowed
        or values.get("API_SERVER_ENABLED") != "true"
        or values.get("API_SERVER_HOST") != "0.0.0.0"  # noqa: S104 - parsed profile value
        or len(key) < 8
        or not 1024 <= port <= 65535
    ):
        raise ControlError(HTTPStatus.BAD_REQUEST, "Staged profile is invalid")
    return key, port


def _read_runtime_profile_files(profile_directory: Path) -> dict[str, bytes]:
    profile_fd = -1
    try:
        profile_fd = _open_directory(profile_directory)
        files: dict[str, bytes] = {}
        total = 0
        for name in PROFILE_FILES:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=profile_fd,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_mode & 0o177
                    or info.st_size > MAX_PROFILE_FILE_BYTES
                ):
                    raise OSError("unsafe runtime profile file")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    content = source.read(MAX_PROFILE_FILE_BYTES + 1)
                if len(content) != info.st_size:
                    raise OSError("runtime profile changed while reading")
                content.decode("utf-8")
                total += len(content)
                if total > MAX_PROFILE_BYTES:
                    raise OSError("runtime profile exceeds limit")
                files[name] = content
            finally:
                os.close(descriptor)
        return files
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, UnicodeError) as exc:
        raise ControlError(HTTPStatus.CONFLICT, "Runtime profile conflicts with staging") from exc
    finally:
        if profile_fd >= 0:
            os.close(profile_fd)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _read_staged_profile(staging_root: Path, profile_name: str) -> StagedProfile:
    """Read one exact profile bundle through no-follow directory descriptors."""

    if not PROFILE_NAME.fullmatch(profile_name):
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")
    root_fd = profile_fd = -1
    try:
        root_fd = _open_directory(staging_root)
        profile_fd = os.open(
            profile_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        profile_info = os.fstat(profile_fd)
        if not stat.S_ISDIR(profile_info.st_mode) or profile_info.st_mode & 0o027:
            raise OSError("unsafe staged profile directory")
        if set(os.listdir(profile_fd)) != PROFILE_ENTRIES:
            raise OSError("unexpected staged profile entry")

        for directory in PROFILE_DIRECTORIES:
            child_fd = os.open(
                directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=profile_fd,
            )
            try:
                child_info = os.fstat(child_fd)
                if (
                    not stat.S_ISDIR(child_info.st_mode)
                    or child_info.st_mode & 0o027
                    or os.listdir(child_fd)
                ):
                    raise OSError("unsafe staged profile directory")
            finally:
                os.close(child_fd)

        files: dict[str, bytes] = {}
        total = 0
        for name in PROFILE_FILES:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=profile_fd,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_mode & 0o137
                    or info.st_size > MAX_PROFILE_FILE_BYTES
                ):
                    raise OSError("unsafe staged profile file")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    content = source.read(MAX_PROFILE_FILE_BYTES + 1)
                if len(content) != info.st_size:
                    raise OSError("staged profile changed while reading")
                content.decode("utf-8")
                total += len(content)
                if total > MAX_PROFILE_BYTES:
                    raise OSError("staged profile exceeds limit")
                files[name] = content
            finally:
                os.close(descriptor)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, UnicodeError) as exc:
        if isinstance(exc, ControlError):
            raise
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found") from exc
    finally:
        if profile_fd >= 0:
            os.close(profile_fd)
        if root_fd >= 0:
            os.close(root_fd)
    key, port = _runtime_values(files[".env"])
    return StagedProfile(files=files, api_key=key, api_port=port)


def _write_runtime_profile(target: Path, staged: StagedProfile) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.activate-", dir=target.parent))
    os.chmod(temporary, 0o700)
    try:
        for directory in PROFILE_DIRECTORIES:
            (temporary / directory).mkdir(mode=0o700)
        for name, content in staged.files.items():
            path = temporary / name
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
        if target.exists() or target.is_symlink():
            raise FileExistsError("runtime profile appeared during activation")
        os.rename(temporary, target)
        root_fd = _open_directory(target.parent)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def activate_profile(
    staging_root: Path,
    runtime_root: Path,
    profile_name: str,
    staged: StagedProfile,
) -> Path:
    """Atomically import a staged profile or validate the exact existing runtime."""

    with _ACTIVATION_LOCK:
        try:
            runtime_root_real = runtime_root.resolve(strict=True)
            root_info = runtime_root.lstat()
        except (FileNotFoundError, OSError) as exc:
            raise ControlError(
                HTTPStatus.SERVICE_UNAVAILABLE, "Hermes runtime unavailable"
            ) from exc
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, "Hermes runtime unavailable")
        target = runtime_root_real / profile_name
        try:
            existing = _profile_directory(runtime_root_real, profile_name)
        except ControlError as exc:
            if exc.status != HTTPStatus.NOT_FOUND:
                raise
        else:
            runtime_key, runtime_port = _profile_runtime(existing)
            runtime_files = _read_runtime_profile_files(existing)
            if (
                not hmac.compare_digest(runtime_key, staged.api_key)
                or runtime_port != staged.api_port
                or runtime_files != staged.files
            ):
                raise ControlError(HTTPStatus.CONFLICT, "Runtime profile conflicts with staging")
            return existing
        try:
            _write_runtime_profile(target, staged)
        except FileExistsError as exc:
            raise ControlError(
                HTTPStatus.CONFLICT, "Runtime profile conflicts with staging"
            ) from exc
        except OSError as exc:
            raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, "Hermes activation failed") from exc
        return _profile_directory(runtime_root_real, profile_name)


def _profile_key(profile_directory: Path) -> str:
    return _profile_runtime(profile_directory)[0]


def _authorised(header: str | None, expected_key: str) -> bool:
    prefix = "Bearer "
    if not header or not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix) :], expected_key)


def _run_gateway(profile_name: str, action: str, *, timeout_seconds: int = 8) -> None:
    if not PROFILE_NAME.fullmatch(profile_name) or action not in {"start", "restart"}:
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")
    executable = shutil.which("hermes")
    if executable is None:
        raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, "Hermes command unavailable")
    command_environment = {
        "HOME": "/opt/data",
        "HERMES_HOME": "/opt/data",
        "HERMES_WRITE_SAFE_ROOT": "/opt/data",
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
        "PATH": os.environ.get("PATH", "/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    failure_message = f"Hermes {action} failed"
    try:
        completed = subprocess.run(  # noqa: S603 - strict profile regex and fixed argv only
            [executable, "-p", profile_name, "gateway", action],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds,
            close_fds=True,
            cwd="/opt/hermes",
            env=command_environment,
        )
    except subprocess.TimeoutExpired as exc:
        print(
            json.dumps(
                {
                    "event": "hermes_control_lifecycle_failed",
                    "operation": action,
                    "category": "timeout",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, failure_message) from exc
    except OSError as exc:
        print(
            json.dumps(
                {
                    "event": "hermes_control_lifecycle_failed",
                    "operation": action,
                    "category": "execution",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, failure_message) from exc
    if completed.returncode != 0:
        print(
            json.dumps(
                {
                    "event": "hermes_control_lifecycle_failed",
                    "operation": action,
                    "category": "exit_code",
                    "returncode": completed.returncode,
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, failure_message)


def restart_profile(profile_name: str, *, timeout_seconds: int = 8) -> None:
    _run_gateway(profile_name, "restart", timeout_seconds=timeout_seconds)


def start_profile(profile_name: str, *, timeout_seconds: int = 8) -> None:
    _run_gateway(profile_name, "start", timeout_seconds=timeout_seconds)


def ensure_profile_service(profile_name: str) -> None:
    """Register one fixed profile gateway with the container's s6 supervisor."""

    if not PROFILE_NAME.fullmatch(profile_name):
        raise ControlError(HTTPStatus.NOT_FOUND, "Profile not found")
    try:
        from hermes_cli.service_manager import detect_service_manager, get_service_manager

        with _ACTIVATION_LOCK:
            if detect_service_manager() != "s6":
                raise RuntimeError("s6 service manager unavailable")
            manager = get_service_manager()
            if not manager.supports_runtime_registration():
                raise RuntimeError("runtime registration unavailable")
            if profile_name not in manager.list_profile_gateways():
                manager.register_profile_gateway(profile_name, start_now=False)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "hermes_control_lifecycle_failed",
                    "operation": "activate",
                    "category": "registration",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, "Hermes activate failed") from exc


def wait_profile_ready(
    port: int,
    api_key: str,
    *,
    operation: str = "restart",
    timeout_seconds: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1.0)
        try:
            connection.request(
                "GET",
                "/health/detailed",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            )
            response = connection.getresponse()
            payload = response.read(1025)
            if response.status == 200 and len(payload) <= 1024:
                parsed = json.loads(payload)
                if isinstance(parsed, dict) and str(parsed.get("status", "")).lower() in {
                    "ok",
                    "ready",
                    "healthy",
                }:
                    return
        except (OSError, http.client.HTTPException, json.JSONDecodeError):
            pass
        finally:
            connection.close()
        time.sleep(0.25)
    print(
        json.dumps(
            {
                "event": "hermes_control_lifecycle_failed",
                "operation": operation,
                "category": "readiness",
            }
        ),
        file=sys.stderr,
        flush=True,
    )
    raise ControlError(HTTPStatus.SERVICE_UNAVAILABLE, f"Hermes {operation} failed")


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "BumpaBestieHermesControl/1"
    sys_version = ""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            parsed_path = urlsplit(self.path)
            if parsed_path.query or parsed_path.fragment:
                raise ControlError(HTTPStatus.NOT_FOUND, "Not found")
            path = unquote(parsed_path.path)
            restart_match = RESTART_PATH.fullmatch(path)
            activate_match = ACTIVATE_PATH.fullmatch(path)
            if restart_match is None and activate_match is None:
                raise ControlError(HTTPStatus.NOT_FOUND, "Not found")
            length_header = self.headers.get("Content-Length")
            if length_header is None:
                raise ControlError(HTTPStatus.LENGTH_REQUIRED, "Content length required")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ControlError(HTTPStatus.BAD_REQUEST, "Invalid request") from exc
            if not 1 <= length <= MAX_BODY_BYTES:
                raise ControlError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Invalid request")
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ControlError(HTTPStatus.BAD_REQUEST, "Invalid request") from exc
            operation = "restart" if restart_match is not None else "activate"
            if body != {"confirmation": operation}:
                raise ControlError(HTTPStatus.BAD_REQUEST, "Invalid confirmation")
            matched = restart_match or activate_match
            assert matched is not None
            profile_name = matched.group(1)
            if operation == "activate":
                staged = _read_staged_profile(self.server.staging_root, profile_name)  # type: ignore[attr-defined]
                if not _authorised(self.headers.get("Authorization"), staged.api_key):
                    raise ControlError(HTTPStatus.UNAUTHORIZED, "Authentication failed")
                activate_profile(
                    self.server.staging_root,  # type: ignore[attr-defined]
                    self.server.profile_root,  # type: ignore[attr-defined]
                    profile_name,
                    staged,
                )
                profile_key, profile_port = staged.api_key, staged.api_port
                ensure_profile_service(profile_name)
                start_profile(profile_name)
            else:
                profile_dir = _profile_directory(self.server.profile_root, profile_name)  # type: ignore[attr-defined]
                profile_key, profile_port = _profile_runtime(profile_dir)
                if not _authorised(self.headers.get("Authorization"), profile_key):
                    raise ControlError(HTTPStatus.UNAUTHORIZED, "Authentication failed")
                restart_profile(profile_name)
            wait_profile_ready(profile_port, profile_key, operation=operation)
            self._json(
                HTTPStatus.OK,
                {"status": "activated" if operation == "activate" else "restarted"},
            )
        except ControlError as exc:
            self._json(exc.status, {"detail": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if urlsplit(self.path).path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def log_message(self, format: str, *args: object) -> None:
        # Request paths contain only validated profile names, but suppressing the
        # standard access log keeps lifecycle operations out of container logs.
        return

    def _json(self, status: HTTPStatus, payload: dict[str, str]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    profile_root = Path(os.environ.get("HERMES_PROFILE_ROOT", "/opt/data/profiles"))
    staging_root = Path(os.environ.get("HERMES_STAGING_ROOT", "/staged/profiles"))
    port = int(os.environ.get("HERMES_CONTROL_PORT", "8699"))
    if not 1024 <= port <= 65535:
        raise SystemExit("Invalid Hermes control port")
    # Reachable only on Compose's unexposed internal `app` network.
    server = ThreadingHTTPServer(("0.0.0.0", port), ControlHandler)  # noqa: S104
    server.profile_root = profile_root  # type: ignore[attr-defined]
    server.staging_root = staging_root  # type: ignore[attr-defined]
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
