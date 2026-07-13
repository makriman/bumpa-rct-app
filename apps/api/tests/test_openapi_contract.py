from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.openapi_contract import (
    ContractDriftError,
    check_contract,
    contract_application,
    contract_document,
    render_contract,
)


def test_contract_is_deterministic_complete_and_not_publicly_routed() -> None:
    first_app = contract_application()
    second_app = contract_application()

    first = render_contract(contract_document(first_app))
    second = render_contract(contract_document(second_app))

    assert first == second
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert (
        json.dumps(json.loads(first), ensure_ascii=False, indent=2, sort_keys=True) + "\n" == first
    )
    assert first_app.openapi_url is None
    assert first_app.docs_url is None
    assert first_app.redoc_url is None

    document = json.loads(first)
    assert document["info"] == {
        "title": "Bumpa Bestie API",
        "version": "0.1.0",
    }
    direct_paths = {
        route.path
        for route in first_app.routes
        if isinstance(route, APIRoute) and route.include_in_schema
    }
    assert direct_paths <= set(document["paths"])
    assert all(
        any(path.startswith(prefix) for path in document["paths"])
        for prefix in ("/v1/auth", "/v1/settings", "/v1/admin", "/v1/research", "/webhooks")
    )

    operation_ids = [
        operation["operationId"]
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert operation_ids
    assert len(operation_ids) == len(set(operation_ids))


def test_contract_strips_examples_and_never_serializes_generation_secrets() -> None:
    application = FastAPI()

    class ExampleNamedProperty(BaseModel):
        examples: list[str]

    @application.get(
        "/example",
        openapi_extra={
            "example": {"access_token": "must-not-be-checked-in"},
            "examples": [{"phone_e164": "+15555550100"}],
            "x-retained-contract-field": True,
        },
    )
    def example() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/legitimate-property", response_model=ExampleNamedProperty)
    def legitimate_property() -> ExampleNamedProperty:
        return ExampleNamedProperty(examples=["contract data, not an annotation"])

    redacted_document = contract_document(application)
    redacted = render_contract(redacted_document)
    assert '"example"' not in redacted
    assert (
        redacted_document["components"]["schemas"]["ExampleNamedProperty"]["properties"][
            "examples"
        ]["type"]
        == "array"
    )
    assert '"x-retained-contract-field": true' in redacted
    assert "must-not-be-checked-in" not in redacted
    assert "+15555550100" not in redacted

    contract = render_contract(contract_document())
    assert '"example"' not in contract
    assert '"examples"' not in contract
    assert "contract-generation-" not in contract
    assert str(Path.home()) not in contract


def test_contract_check_detects_and_explains_drift(tmp_path: Path) -> None:
    contract_path = tmp_path / "openapi.json"
    current = render_contract()
    contract_path.write_text(current, encoding="utf-8")
    check_contract(contract_path)

    contract_path.write_text(
        current.replace('"openapi": "3.1.0"', '"openapi": "0.0.0"'), encoding="utf-8"
    )
    with pytest.raises(ContractDriftError, match="OpenAPI contract drift detected") as error:
        check_contract(contract_path)

    assert "---" in str(error.value)
    assert "+++" in str(error.value)


def test_contract_check_reports_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(ContractDriftError, match="run `make api-contract`"):
        check_contract(tmp_path / "missing.json")
