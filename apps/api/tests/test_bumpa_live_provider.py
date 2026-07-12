from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.providers.bumpa import BumpaClient, BumpaProviderError, _normalise_order

FIXTURES = Path(__file__).parents[3] / "tests" / "contract" / "fixtures" / "bumpa"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, base_url="https://api.getbumpa.com/api")


def test_live_bumpa_sync_reads_all_datasets_and_order_pages_without_key_leakage() -> None:
    page_one = json.loads((FIXTURES / "orders_page_1.json").read_text())
    page_two = json.loads((FIXTURES / "orders_page_2.json").read_text())
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "super-secret-test-key"
        assert request.url.host == "api.getbumpa.com"
        if request.url.path.endswith("/orders"):
            page = request.url.params.get("page")
            return httpx.Response(200, json=page_one if page == "1" else page_two)
        dataset = request.url.params["dataset"]
        return httpx.Response(
            200,
            json={"data": {"value": "12500.50"}, "dataset": dataset},
            headers={"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "88"},
        )

    http = _client(httpx.MockTransport(respond))
    with BumpaClient(
        "super-secret-test-key",
        "business_id",
        "business-test",
        client=http,
        sleep=lambda _seconds: None,
    ) as provider:
        result = provider.sync(date(2026, 1, 1), date(2026, 1, 31))

    assert len(result.datasets) == 10
    assert all(row.value is not None for row in result.datasets)
    assert len(result.orders) == 2
    assert result.orders[1].status == "future_status_is_preserved"
    assert str(result.orders[1].total_amount) == "0.00"
    assert result.rate_limit_limit == 100
    assert result.rate_limit_remaining == 88
    assert len([request for request in requests if request.url.path.endswith("/orders")]) == 2
    assert "super-secret-test-key" not in repr(result)


def test_live_bumpa_retries_rate_limits_and_bounds_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "slow down"}, headers={"Retry-After": "90"})
        return httpx.Response(200, json={"data": {"value": "4"}})

    provider = BumpaClient(
        "secret",
        "location_id",
        "location-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=sleeps.append,
    )
    status, payload, _headers = provider.get_analytics(
        "sales", "overview", date(2026, 1, 1), date(2026, 1, 1)
    )
    assert status == 200 and payload["data"]["value"] == "4"
    assert calls == 2 and sleeps == [10.0]


def test_live_bumpa_timeout_exhausts_bounded_retry_budget_with_sanitized_error() -> None:
    secret = "never-include-this-key"
    private_detail = "private-timeout-detail-must-not-escape"
    calls = 0
    sleeps: list[float] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(private_detail, request=request)

    provider = BumpaClient(
        secret,
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(timeout)),
        sleep=sleeps.append,
        max_attempts=3,
    )

    with pytest.raises(BumpaProviderError, match="temporarily unreachable") as raised:
        provider.get_analytics("sales", "overview", date(2026, 1, 1), date(2026, 1, 1))

    assert calls == 3
    assert sleeps == [1, 2]
    assert raised.value.status_code is None
    assert raised.value.retryable is True
    assert secret not in str(raised.value)
    assert private_detail not in str(raised.value)
    assert isinstance(raised.value.__cause__, httpx.ReadTimeout)


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (429, "rate limit"),
        (500, "temporarily unavailable"),
        (503, "temporarily unavailable"),
    ],
)
def test_live_bumpa_exhausted_transient_responses_raise_retryable_error(
    status_code: int, message: str
) -> None:
    secret = "never-include-this-key"
    calls = 0

    def unavailable(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            json={"error": f"upstream detail containing {secret}"},
            headers={"Retry-After": "0"},
        )

    provider = BumpaClient(
        secret,
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(unavailable)),
        sleep=lambda _seconds: None,
        max_attempts=3,
    )

    with pytest.raises(BumpaProviderError, match=message) as raised:
        provider.get_analytics("sales", "overview", date(2026, 1, 1), date(2026, 1, 1))

    assert calls == 3
    assert raised.value.status_code == status_code
    assert raised.value.retryable is True
    assert secret not in str(raised.value)


@pytest.mark.parametrize("status_code", [401, 403])
def test_live_bumpa_auth_responses_fail_immediately_and_non_retryably(status_code: int) -> None:
    secret = "never-include-this-key"
    calls = 0

    def unauthorized(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"error": f"invalid {secret}"})

    provider = BumpaClient(
        secret,
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(unauthorized)),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(BumpaProviderError, match="authentication failed") as raised:
        provider.get_analytics("sales", "overview", date(2026, 1, 1), date(2026, 1, 1))

    assert calls == 1
    assert raised.value.status_code == status_code
    assert raised.value.retryable is False
    assert secret not in str(raised.value)


def test_live_bumpa_preserves_dataset_level_unavailable_semantics() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(
                200,
                json={
                    "data": [],
                    "pagination": {"current_page": 1, "last_page": 1},
                },
            )
        if request.url.params["dataset"] == "least_selling_products":
            return httpx.Response(200, json={"error": "Dataset is not available for this shop"})
        return httpx.Response(200, json={"data": {"value": "4"}})

    provider = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    )

    result = provider.sync(date(2026, 1, 1), date(2026, 1, 1))

    unavailable = [dataset for dataset in result.datasets if dataset.availability == "unavailable"]
    assert len(unavailable) == 1
    assert unavailable[0].dataset == "least_selling_products"
    assert unavailable[0].value is None
    assert result.orders_availability == "available"


def test_live_bumpa_preserves_nonretryable_orders_failure_as_partial_state() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(422, json={"error": "Orders scope is unavailable"})
        return httpx.Response(200, json={"data": {"value": "4"}})

    provider = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    )

    result = provider.sync(date(2026, 1, 1), date(2026, 1, 1))

    assert result.orders == []
    assert result.orders_availability == "error"
    assert result.orders_error == "Orders scope is unavailable"


def test_live_bumpa_errors_are_sanitized_and_invalid_contracts_fail_closed() -> None:
    secret = "never-include-this-key"

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"not-json")

    provider = BumpaClient(
        secret,
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(malformed)),
        sleep=lambda _seconds: None,
        max_attempts=1,
    )
    with pytest.raises(BumpaProviderError) as raised:
        provider.get_orders_page(date(2026, 1, 1), date(2026, 1, 2), 1)
    assert secret not in str(raised.value)
    with pytest.raises(ValueError, match="scope_type"):
        BumpaClient("secret", "tenant_id", "x")
    with pytest.raises(ValueError, match="dataset"):
        provider.get_analytics("sales", "unsupported", date.today(), date.today())


def test_live_bumpa_does_not_invent_zero_for_missing_order_money() -> None:
    order = _normalise_order({"id": "order-without-money", "status": "pending"})

    assert order.total_amount is None
