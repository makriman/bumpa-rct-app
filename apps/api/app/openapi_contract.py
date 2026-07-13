from __future__ import annotations

import argparse
import difflib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI

from app.core.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "openapi.json"

# Schema extraction must not depend on a developer's .env or production secrets.
# These inert settings satisfy production hardening while all providers remain
# disabled. They are never rendered into the OpenAPI document.
_CONTRACT_SETTINGS: Mapping[str, Any] = {
    "app_name": "Bumpa Bestie API",
    "app_env": "production",
    "api_prefix": "/v1",
    "jwt_secret": "contract-generation-jwt-secret-000000000000",
    "otp_secret": "contract-generation-otp-secret-000000000000",
    "field_encryption_key": "contract-generation-field-key-00000000000",
    "research_pseudonym_key": "contract-generation-pseudonym-key-000000000",
    "onboarding_integrity_key": "contract-generation-onboarding-key-00000000",
    "auth_rate_limit_enabled": True,
    "operation_rate_limit_enabled": True,
    "expose_local_otp": False,
    "seed_demo_data": False,
    "whatsapp_backend": "disabled",
    "bumpa_backend": "disabled",
    "agent_backend": "disabled",
}

_CONTRACT_IMPORT_ENVIRONMENT = {
    key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
    for key, value in _CONTRACT_SETTINGS.items()
}


class ContractDriftError(RuntimeError):
    """The checked-in contract does not match the current FastAPI application."""


def contract_application() -> FastAPI:
    """Build the canonical production-shaped app without exposing schema routes."""

    settings = Settings.model_validate(_CONTRACT_SETTINGS)
    # Importing app.main normally constructs the ASGI singleton. On a standalone
    # generator invocation, isolate that import from both a developer's .env and
    # any injected production credentials. If the server module is already
    # loaded, this context is a no-op around Python's cached import.
    with (
        patch.dict(os.environ, _CONTRACT_IMPORT_ENVIRONMENT, clear=True),
        patch.dict(Settings.model_config, {"env_file": None}),
    ):
        from app.main import create_app

    return create_app(settings_config=settings)


def _without_examples(value: Any) -> Any:
    """Remove potentially sensitive examples while preserving contract semantics."""

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if key in {"example", "examples"}:
                continue
            if key == "properties" and isinstance(child, dict):
                # `example` and `examples` are valid JSON field names. Preserve
                # keys in a schema's property-name map while still removing
                # example annotations from each property's schema.
                normalized[key] = {
                    property_name: _without_examples(property_schema)
                    for property_name, property_schema in child.items()
                }
            else:
                normalized[key] = _without_examples(child)
        return normalized
    if isinstance(value, list):
        return [_without_examples(child) for child in value]
    return value


def contract_document(application: FastAPI | None = None) -> dict[str, Any]:
    """Return the redacted, canonical OpenAPI document used by code generation."""

    document = (application or contract_application()).openapi()
    normalized = _without_examples(document)
    if not isinstance(normalized, dict):  # pragma: no cover - FastAPI invariant
        raise TypeError("FastAPI generated a non-object OpenAPI document")
    return normalized


def render_contract(document: Mapping[str, Any] | None = None) -> str:
    """Serialize with stable key ordering and exactly one trailing newline."""

    payload = document if document is not None else contract_document()
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_contract(path: Path = DEFAULT_CONTRACT_PATH) -> None:
    """Atomically replace the checked-in contract with the canonical document."""

    rendered = render_contract()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def check_contract(path: Path = DEFAULT_CONTRACT_PATH) -> None:
    """Fail with a reviewable diff when source and checked-in schema diverge."""

    expected = render_contract()
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ContractDriftError(
            f"OpenAPI contract is missing at {path}; run `make api-contract`."
        ) from exc
    if actual == expected:
        return
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(path),
            tofile="current FastAPI schema",
            n=3,
        )
    )
    raise ContractDriftError(
        "OpenAPI contract drift detected; run `make api-contract` and review the diff.\n" + diff
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or verify the API contract")
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("--output", type=Path, default=DEFAULT_CONTRACT_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "generate":
        write_contract(args.output)
        print(f"Wrote deterministic OpenAPI contract to {args.output}")
        return 0
    try:
        check_contract(args.output)
    except ContractDriftError as exc:
        print(str(exc))
        return 1
    print(f"OpenAPI contract is current: {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the Make target
    raise SystemExit(main())
