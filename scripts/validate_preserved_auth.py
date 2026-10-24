"""Verify an unchanged expired auth boundary captured before target selection."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

RELEASE_KEYS = frozenset(
    {
        "DEPLOY_REF",
        "IMAGE_TAG",
        "INFRA_IMAGE_TAG",
        "API_IMAGE",
        "WEB_IMAGE",
        "ADMIN_WEB_IMAGE",
        "RESEARCH_WEB_IMAGE",
        "CADDY_IMAGE",
        "POSTGRES_IMAGE",
        "BACKUP_IMAGE",
        "HERMES_IMAGE",
    }
)


def configuration_digest(data: bytes) -> str:
    return hashlib.sha256(
        b"".join(
            line
            for line in data.splitlines(keepends=True)
            if line.split(b"=", 1)[0] not in {key.encode("ascii") for key in RELEASE_KEYS}
        )
    ).hexdigest()


def validate(env_path: Path, record_path: Path, running_api_id: str) -> None:
    env_stat, record_stat = env_path.lstat(), record_path.lstat()
    for current in (env_stat, record_stat):
        if not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o600:
            raise ValueError(
                "Preserved auth requires private regular environment and release files"
            )
    if env_stat.st_uid != record_stat.st_uid or env_stat.st_gid != record_stat.st_gid:
        raise ValueError("Preserved auth files must have matching ownership")
    data = env_path.read_bytes()
    env: dict[str, str] = {}
    for line in data.decode().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in env:
            raise ValueError("Malformed or duplicate environment setting")
        env[key] = value
    record = json.loads(record_path.read_text())
    if not isinstance(record, dict) or not isinstance(record.get("auth"), dict):
        raise ValueError("Explicit recorded auth is required")
    auth = record.get("auth", {})
    fields = {
        "AUTH_LOGIN_MODE": "login_mode",
        "TEMPORARY_WEB_PIN_VERIFIER_FILE": "temporary_web_pin_verifier_file",
        "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST": "temporary_web_pin_verifier_file_host",
        "TEMPORARY_WEB_PIN_EXPIRES_AT": "temporary_web_pin_expires_at",
        "WHATSAPP_BACKEND": "whatsapp_backend",
    }
    if (
        auth.get("login_mode") != "temporary_static_pin"
        or env.get("TEMPORARY_WEB_PIN_VERIFIER") != ""
    ):
        raise ValueError(
            "Preservation applies only to the explicit recorded file-backed PIN boundary"
        )
    if any(key not in env or env[key] != auth.get(field) for key, field in fields.items()):
        raise ValueError("Recorded auth selectors changed")
    expires = datetime.fromisoformat(env["TEMPORARY_WEB_PIN_EXPIRES_AT"].replace("Z", "+00:00"))
    if expires.tzinfo is None or expires.utcoffset() is None or expires > datetime.now(timezone.utc):
        raise ValueError("Preservation requires a genuinely expired timezone-aware prior deadline")
    proof = record.get("preserved_configuration", {})
    if not isinstance(proof, dict):
        raise ValueError("Invalid preservation proof")
    if (
        proof.get("schema_version") != 1
        or proof.get("policy") != "unchanged-expired-pin-no-new-logins"
    ):
        raise ValueError("Missing observed-baseline preservation proof")
    if (
        not re.fullmatch(r"sha256:[a-f0-9]{64}", running_api_id)
        or proof.get("running_api_image_id") != running_api_id
    ):
        raise ValueError("Observed prior API image does not match preservation proof")
    if proof.get("non_release_environment_sha256") != configuration_digest(data):
        raise ValueError("Nonrelease configuration changed from the captured prior baseline")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: validate_preserved_auth.py ENV RECORD RUNNING_API_IMAGE_ID")
    try:
        validate(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit(
            "Recorded expired-auth preservation proof is missing, unsafe or mismatched"
        ) from None
    print("Preserving the exact recorded expired auth boundary; new sign-ins remain unavailable.")


if __name__ == "__main__":
    main()
