#!/usr/bin/env python3
"""Send a bounded, HMAC-signed host operations event to one fixed HTTPS webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import ssl
import stat
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 32_768
MAX_OUTPUT_BYTES = 16_384
MAX_RESPONSE_BYTES = 65_536
SAFE_STATUS = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
UTC = timezone.utc


def load_fixed_config(path_value: str) -> dict[str, object]:
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError("alert config file path must be absolute")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("alert config file must be a regular file")
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise ValueError("alert config file exceeds limit")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("alert config file must contain an object")
    allowed = {"webhook_url", "hmac_secret_file", "max_attempts", "timeout_seconds"}
    if set(parsed) - allowed:
        raise ValueError("alert config file contains unknown fields")
    return parsed


def load_secret(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError("alert secret file path must be absolute")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("alert secret file must be a regular file")
    if metadata.st_mode & 0o077:
        raise ValueError("alert secret file permissions are too broad")
    value = path.read_text(encoding="utf-8").rstrip("\r\n")
    if len(value) < 32 or "\n" in value or "\r" in value:
        raise ValueError("alert secret file is invalid")
    return value


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if not (
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path not in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    ):
        raise ValueError("alert webhook must be an uncredentialed HTTPS URL")
    return value


def sanitize_event(event_name: str, source: dict[str, Any]) -> dict[str, object]:
    if event_name == "disk_usage":
        status = _safe_status(source.get("status"))
        filesystems = source.get("filesystems")
        rows = filesystems if isinstance(filesystems, list) else []
        block_percent = 0
        inode_percent = 0
        for row in rows[:32]:
            if not isinstance(row, dict):
                continue
            blocks = row.get("blocks")
            inodes = row.get("inodes")
            block_percent = max(block_percent, _safe_percent(blocks))
            inode_percent = max(inode_percent, _safe_percent(inodes))
        attributes: dict[str, object] = {
            "block_used_percent": block_percent,
            "checked_filesystems": min(len(rows), 32),
            "error_count": min(len(source.get("errors", [])), 32)
            if isinstance(source.get("errors"), list)
            else 0,
            "inode_used_percent": inode_percent,
            "status": status,
            "threshold_percent": _bounded_int(
                source.get("threshold_percent"), 1, 100, 85
            ),
        }
        event_type = "disk_capacity_failure"
        summary = "Host disk or inode capacity needs operator attention"
        severity = "critical" if max(block_percent, inode_percent) >= 95 else "high"
        occurred_at = _timestamp(source.get("checked_at"))
    elif event_name == "backup":
        status = _safe_status(source.get("status"))
        attributes = {"status": status}
        event_type = "backup_success" if status == "success" else "backup_failure"
        summary = (
            "Scheduled local backup completed"
            if status == "success"
            else "Scheduled local backup needs operator attention"
        )
        severity = "info" if status == "success" else "critical"
        occurred_at = _timestamp(source.get("occurred_at"))
    else:
        raise ValueError("unsupported host alert event")

    canonical = json.dumps(
        {
            "event_type": event_type,
            "occurred_at": occurred_at,
            "attributes": attributes,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    event_id = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": event_type,
        "severity": severity,
        "occurred_at": occurred_at,
        "service": "host",
        "summary": summary,
        "attributes": attributes,
    }


def signed_request(
    endpoint: str,
    secret: str,
    envelope: dict[str, object],
) -> urllib.request.Request:
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    if len(body) > MAX_OUTPUT_BYTES:
        raise ValueError("alert payload exceeds limit")
    occurred_at = str(envelope["occurred_at"])
    signature = hmac.new(
        secret.encode(),
        occurred_at.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": str(envelope["event_id"]),
            "User-Agent": "BumpaBestie-HostAlerts/1.0",
            "X-BumpaBestie-Signature": f"v1={signature}",
            "X-BumpaBestie-Timestamp": occurred_at,
        },
    )


def send_with_retries(
    request: urllib.request.Request,
    *,
    attempts: int,
    timeout_seconds: float,
    opener: Any = None,
) -> bool:
    open_request = opener or urllib.request.urlopen
    for attempt in range(attempts):
        try:
            with open_request(
                request,
                timeout=timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                if len(response.read(MAX_RESPONSE_BYTES + 1)) > MAX_RESPONSE_BYTES:
                    return False
                return 200 <= int(response.status) < 300
        except urllib.error.HTTPError as exc:
            exc.read(MAX_RESPONSE_BYTES + 1)
            retryable = exc.code in {408, 425, 429} or exc.code >= 500
            if not retryable:
                return False
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if attempt + 1 < attempts:
            time.sleep(min(0.5 * (2**attempt), 2.0))
    return False


def main() -> int:
    try:
        config = load_fixed_config(
            os.environ.get(
                "BUMPABESTIE_ALERT_CONFIG_FILE", "/etc/bumpabestie/alerts.json"
            )
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 2
    endpoint_value = os.environ.get("BUMPABESTIE_ALERT_WEBHOOK_URL", "") or str(
        config.get("webhook_url", "")
    )
    secret_path = os.environ.get("BUMPABESTIE_ALERT_HMAC_SECRET_FILE", "") or str(
        config.get("hmac_secret_file", "")
    )
    if not endpoint_value and not secret_path:
        return 0
    try:
        endpoint = validate_endpoint(endpoint_value)
        secret = load_secret(secret_path)
        attempts = _bounded_int(
            os.environ.get(
                "BUMPABESTIE_ALERT_MAX_ATTEMPTS", config.get("max_attempts")
            ),
            1,
            5,
            3,
        )
        timeout_seconds = float(
            os.environ.get(
                "BUMPABESTIE_ALERT_TIMEOUT_SECONDS", config.get("timeout_seconds", 10)
            )
        )
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("alert timeout is invalid")
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("host alert input exceeds limit")
        source = json.loads(raw)
        if not isinstance(source, dict):
            raise ValueError("host alert input must be an object")
        event_name = os.environ.get("BUMPABESTIE_ALERT_EVENT", "")
        envelope = sanitize_event(event_name, source)
        request = signed_request(endpoint, secret, envelope)
        return (
            0
            if send_with_retries(
                request,
                attempts=attempts,
                timeout_seconds=timeout_seconds,
            )
            else 1
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 2


def _timestamp(value: object) -> str:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else None
        )
    except ValueError:
        parsed = None
    current = parsed or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _safe_status(value: object) -> str:
    return value if isinstance(value, str) and SAFE_STATUS.fullmatch(value) else "error"


def _safe_percent(resource: object) -> int:
    return (
        _bounded_int(resource.get("used_percent"), 0, 100, 0)
        if isinstance(resource, dict)
        else 0
    )


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


if __name__ == "__main__":
    sys.exit(main())
