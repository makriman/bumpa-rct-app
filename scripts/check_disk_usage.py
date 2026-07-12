#!/usr/bin/env python3
"""Check host filesystem capacity and emit one journal-friendly JSON event.

The optional alert hook is an operator-supplied executable. It receives the
same JSON event on stdin only when a filesystem is near full or cannot be
checked. No application environment file or credentials are loaded here.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_THRESHOLD_PERCENT = 85
HOOK_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ResourceUsage:
    available: int
    total: int
    used: int
    used_percent: int


@dataclass(frozen=True)
class FilesystemUsage:
    aliases: list[str]
    blocks: ResourceUsage
    device_id: int
    inodes: ResourceUsage | None


@dataclass(frozen=True)
class CheckError:
    error: str
    path: str


def used_percent(used: int, available: int) -> int:
    """Return df-style integer use%, rounded up to avoid hiding a full edge."""
    denominator = used + available
    if denominator <= 0:
        return 100
    return min(100, (used * 100 + denominator - 1) // denominator)


def resource_usage(total: int, free: int, available: int) -> ResourceUsage:
    used = max(0, total - free)
    available = max(0, available)
    return ResourceUsage(
        available=available,
        total=max(0, total),
        used=used,
        used_percent=used_percent(used, available),
    )


def inspect_filesystems(
    paths: Sequence[str],
) -> tuple[list[FilesystemUsage], list[CheckError]]:
    by_device: dict[int, FilesystemUsage] = {}
    errors: list[CheckError] = []

    for configured_path in paths:
        path = configured_path
        try:
            path = str(Path(configured_path).resolve(strict=False))
            metadata = os.stat(path)
        except OSError as exc:
            errors.append(CheckError(error=exc.__class__.__name__, path=path))
            continue

        existing = by_device.get(metadata.st_dev)
        if existing is not None:
            if path not in existing.aliases:
                existing.aliases.append(path)
            continue

        try:
            fs = os.statvfs(path)
        except OSError as exc:
            errors.append(CheckError(error=exc.__class__.__name__, path=path))
            continue

        fragment_size = fs.f_frsize or fs.f_bsize
        blocks = resource_usage(
            total=fs.f_blocks * fragment_size,
            free=fs.f_bfree * fragment_size,
            available=fs.f_bavail * fragment_size,
        )
        inodes = None
        if fs.f_files > 0:
            inodes = resource_usage(
                total=fs.f_files,
                free=fs.f_ffree,
                available=fs.f_favail,
            )
        by_device[metadata.st_dev] = FilesystemUsage(
            aliases=[path],
            blocks=blocks,
            device_id=metadata.st_dev,
            inodes=inodes,
        )

    return list(by_device.values()), errors


def is_near_full(filesystem: FilesystemUsage, threshold_percent: int) -> bool:
    return filesystem.blocks.used_percent >= threshold_percent or (
        filesystem.inodes is not None and filesystem.inodes.used_percent >= threshold_percent
    )


def build_event(
    paths: Sequence[str], threshold_percent: int, checked_at: datetime | None = None
) -> tuple[dict[str, object], int]:
    filesystems, errors = inspect_filesystems(paths)
    near_full = [
        filesystem for filesystem in filesystems if is_near_full(filesystem, threshold_percent)
    ]
    if errors:
        status = "error"
        exit_code = 2
    elif near_full:
        status = "alert"
        exit_code = 1
    else:
        status = "ok"
        exit_code = 0

    timestamp = checked_at or datetime.now(timezone.utc)  # noqa: UP017 (Python 3.9)
    event: dict[str, object] = {
        "checked_at": timestamp.isoformat().replace("+00:00", "Z"),
        "errors": [asdict(error) for error in errors],
        "event": "disk_usage_check",
        "filesystems": [asdict(filesystem) for filesystem in filesystems],
        "host": socket.gethostname(),
        "near_full_device_ids": [filesystem.device_id for filesystem in near_full],
        "status": status,
        "threshold_percent": threshold_percent,
    }
    return event, exit_code


def invoke_alert_hook(hook: str, event_json: str) -> bool:
    path = Path(hook)
    hook_environment = {
        key: os.environ[key]
        for key in ("HOME", "LANG", "LC_ALL", "PATH", "TZ")
        if key in os.environ
    }
    hook_environment["BUMPABESTIE_ALERT_EVENT"] = "disk_usage"
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if (
        not path.is_absolute()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not os.access(path, os.X_OK)
    ):
        return False

    try:
        # The absolute, non-symlink, executable regular file check above is the
        # trust boundary for an operator-configured hook.
        completed = subprocess.run(  # noqa: S603
            [str(path)],
            input=f"{event_json}\n",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=HOOK_TIMEOUT_SECONDS,
            check=False,
            env=hook_environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def parse_paths(cli_paths: Sequence[str] | None) -> list[str]:
    if cli_paths:
        return list(cli_paths)
    configured = os.environ.get("BUMPABESTIE_DISK_PATHS", "/")
    paths = [path for path in configured.split(os.pathsep) if path]
    if not paths:
        raise ValueError("at least one disk path is required")
    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="absolute path to check; repeat for multiple filesystems",
    )
    parser.add_argument(
        "--threshold-percent",
        type=int,
        default=int(
            os.environ.get("BUMPABESTIE_DISK_THRESHOLD_PERCENT", DEFAULT_THRESHOLD_PERCENT)
        ),
    )
    parser.add_argument(
        "--alert-hook",
        default=os.environ.get("BUMPABESTIE_ALERT_HOOK", ""),
        help="optional absolute executable path; JSON alert is written to stdin",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if not 1 <= args.threshold_percent <= 100:
            raise ValueError("threshold percent must be between 1 and 100")
        paths = parse_paths(args.paths)
        if any(not Path(path).is_absolute() for path in paths):
            raise ValueError("disk paths must be absolute")
    except (ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "event": "disk_usage_check",
                    "error": str(exc),
                    "status": "configuration_error",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2

    event, exit_code = build_event(paths, args.threshold_percent)
    event_json = json.dumps(event, separators=(",", ":"), sort_keys=True)
    print(event_json, flush=True)

    if exit_code != 0 and args.alert_hook:
        if not invoke_alert_hook(args.alert_hook, event_json):
            print(
                json.dumps(
                    {"event": "disk_usage_alert_hook", "status": "error"},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                flush=True,
            )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
