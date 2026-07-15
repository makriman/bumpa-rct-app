from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.providers.bumpa import (
    BumpaClient as ProductionBumpaClient,
)
from app.providers.bumpa import (
    BumpaProviderError,
    _normalise_order,
)
from app.providers.bumpa import (
    decode_analytics_dataset as production_decode_analytics_dataset,
)
from app.providers.redaction import redact_bumpa_payload

FIXTURES = Path(__file__).parents[3] / "tests" / "contract" / "fixtures" / "bumpa"
ANALYTICS = json.loads((FIXTURES / "analytics_responses.json").read_text())


class BumpaClient(ProductionBumpaClient):
    """Lagos/NGN test fixture client; production requires explicit store context."""

    def __init__(
        self,
        api_key: str,
        scope_type: str,
        scope_id: str,
        *,
        store_timezone: str = "Africa/Lagos",
        store_currency: str = "NGN",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            api_key,
            scope_type,
            scope_id,
            store_timezone=store_timezone,
            store_currency=store_currency,
            **kwargs,
        )


def decode_analytics_dataset(
    area: str, dataset: str, status_code: int, payload: dict[str, Any]
) -> Any:
    return production_decode_analytics_dataset(
        area,
        dataset,
        status_code,
        payload,
        store_timezone="Africa/Lagos",
        store_currency="NGN",
    )


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, base_url="https://api.getbumpa.com/api")


def _set_analytics_range(
    payload: dict[str, object],
    date_range: dict[str, str],
    *,
    timezone_name: str = "Africa/Lagos",
) -> None:
    payload["range"] = date_range
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        return
    period = [date_range["from"], date_range["to"]]
    chart = raw_data.get("chart")
    if isinstance(chart, dict) and isinstance(chart.get("current_period"), dict):
        chart["current_period"]["range"] = period
        previous = chart.get("previous_period")
        if isinstance(previous, dict):
            timezone = ZoneInfo(timezone_name)
            current_from = datetime.fromisoformat(date_range["from"]).astimezone(timezone).date()
            current_to = datetime.fromisoformat(date_range["to"]).astimezone(timezone).date()
            days = (current_to - current_from).days + 1
            previous_to = current_from - timedelta(days=1)
            previous_from = previous_to - timedelta(days=days - 1)
            previous["range"] = list(
                _inclusive_local_day_range(previous_from, previous_to, timezone_name).values()
            )
    for key in ("total_products_chart", "total_products_sold_chart"):
        if isinstance(raw_data.get(key), dict):
            raw_data[key]["range"] = period


def _analytics_response(request: httpx.Request) -> httpx.Response:
    area = request.url.path.rsplit("/", 1)[-1]
    dataset = request.url.params["dataset"]
    payload = deepcopy(ANALYTICS[f"{area}.{dataset}"])
    if "range" in payload:
        expected = _inclusive_local_day_range(
            date.fromisoformat(request.url.params["from"]),
            date.fromisoformat(request.url.params["to"]),
            "Africa/Lagos",
        )
        _set_analytics_range(
            payload,
            expected,
        )
    return httpx.Response(200, json=payload)


def _empty_orders() -> dict[str, object]:
    return {
        "success": True,
        "orders": {
            "current_page": 1,
            "last_page": 1,
            "per_page": 100,
            "total": 0,
            "data": [],
        },
    }


def _inclusive_local_day_range(
    date_from: date, date_to: date, timezone_name: str
) -> dict[str, str]:
    timezone = ZoneInfo(timezone_name)
    return {
        "from": datetime.combine(date_from, datetime.min.time(), timezone)
        .astimezone(UTC)
        .isoformat(),
        "to": datetime.combine(date_to, datetime.max.time(), timezone).astimezone(UTC).isoformat(),
    }


def test_live_bumpa_sync_reads_all_datasets_and_order_pages_without_key_leakage() -> None:
    page_one = json.loads((FIXTURES / "orders_page_1.json").read_text())
    page_two = json.loads((FIXTURES / "orders_page_2.json").read_text())
    raw_request_id = "bumpa-success-request-A1B2C3D4"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "super-secret-test-key"
        assert request.url.host == "api.getbumpa.com"
        if request.url.path.endswith("/orders"):
            page = request.url.params.get("page")
            return httpx.Response(200, json=page_one if page == "1" else page_two)
        response = _analytics_response(request)
        return httpx.Response(
            response.status_code,
            json=response.json(),
            headers={
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "88",
                "X-Request-ID": raw_request_id,
            },
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
    assert all(
        row.value is not None
        or row.availability == "unavailable"
        or row.canonical_payload.get("kind") == "ranking"
        for row in result.datasets
    )
    assert len(result.orders) == 2
    assert result.orders[1].status == "future_status_is_preserved"
    assert str(result.orders[1].total_amount) == "0.00"
    assert result.rate_limit_limit == 100
    assert result.rate_limit_remaining == 88
    assert len([request for request in requests if request.url.path.endswith("/orders")]) == 2
    assert "super-secret-test-key" not in repr(result)
    assert raw_request_id not in repr(result)
    assert any(
        response.headers.get("x-request-id-sha256")
        == hashlib.sha256(raw_request_id.encode()).hexdigest()
        for response in result.responses
    )


def test_live_bumpa_accepts_exact_documented_wat_day_window() -> None:
    requested_from = date(2026, 3, 1)
    requested_to = date(2026, 4, 15)
    expected_range = _inclusive_local_day_range(requested_from, requested_to, "Africa/Lagos")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=_empty_orders())
        area = request.url.path.rsplit("/", 1)[-1]
        dataset = request.url.params["dataset"]
        payload = deepcopy(ANALYTICS[f"{area}.{dataset}"])
        if "range" in payload:
            _set_analytics_range(payload, expected_range)
        return httpx.Response(200, json=payload)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(requested_from, requested_to)

    available = [dataset for dataset in result.datasets if dataset.availability == "available"]
    unavailable = [dataset for dataset in result.datasets if dataset.availability == "unavailable"]
    assert len(available) == 8
    assert {dataset.dataset for dataset in unavailable} == {"gross_profit", "net_profit"}
    assert result.orders_availability == "available"
    assert available[0].response_from == datetime.fromisoformat(expected_range["from"])
    assert available[0].response_to == datetime.fromisoformat(expected_range["to"])
    assert available[0].canonical_payload["range"] == {
        "from": requested_from.isoformat(),
        "to": requested_to.isoformat(),
    }


def test_literal_documented_wat_window_preserves_exact_evidence_and_local_dates() -> None:
    requested_from = date(2026, 4, 2)
    requested_to = date(2026, 4, 15)
    literal_range = {
        "from": "2026-04-01T23:00:00.000000Z",
        "to": "2026-04-15T22:59:59.999999Z",
    }
    literal_previous = [
        "2026-03-18T23:00:00.000000Z",
        "2026-04-01T22:59:59.999999Z",
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=_empty_orders())
        area = request.url.path.rsplit("/", 1)[-1]
        dataset = request.url.params["dataset"]
        payload = deepcopy(ANALYTICS[f"{area}.{dataset}"])
        if "range" in payload:
            _set_analytics_range(payload, literal_range)
            raw_data = payload.get("data")
            if isinstance(raw_data, dict):
                chart = raw_data.get("chart")
                if isinstance(chart, dict) and isinstance(chart.get("previous_period"), dict):
                    chart["previous_period"]["range"] = literal_previous
        return httpx.Response(200, json=payload)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(requested_from, requested_to)

    available = [dataset for dataset in result.datasets if dataset.availability == "available"]
    assert len(available) == 8
    for dataset in available:
        assert dataset.response_from == datetime(2026, 4, 1, 23, tzinfo=UTC)
        assert dataset.response_to == datetime(2026, 4, 15, 22, 59, 59, 999999, tzinfo=UTC)
        assert dataset.canonical_payload["range"] == {
            "from": "2026-04-02",
            "to": "2026-04-15",
        }


def test_store_timezone_handles_dst_without_changing_canonical_local_dates() -> None:
    requested_from = date(2026, 3, 28)
    requested_to = date(2026, 3, 30)
    literal_range = {
        "from": "2026-03-28T00:00:00.000000Z",
        "to": "2026-03-30T22:59:59.999999Z",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=_empty_orders())
        area = request.url.path.rsplit("/", 1)[-1]
        dataset = request.url.params["dataset"]
        payload = deepcopy(ANALYTICS[f"{area}.{dataset}"])
        payload = json.loads(json.dumps(payload, ensure_ascii=False).replace("₦", "£"))
        if "range" in payload:
            _set_analytics_range(payload, literal_range, timezone_name="Europe/London")
        return httpx.Response(200, json=payload)

    result = ProductionBumpaClient(
        "secret",
        "business_id",
        "business-test",
        store_timezone="Europe/London",
        store_currency="GBP",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(requested_from, requested_to)

    available = [dataset for dataset in result.datasets if dataset.availability == "available"]
    assert len(available) == 8
    assert available[0].response_from == datetime(2026, 3, 28, tzinfo=UTC)
    assert available[0].response_to == datetime(2026, 3, 30, 22, 59, 59, 999999, tzinfo=UTC)
    assert available[0].canonical_payload["range"] == {
        "from": "2026-03-28",
        "to": "2026-03-30",
    }


@pytest.mark.parametrize(
    ("start_delta", "end_delta"),
    [
        (timedelta(days=-1), timedelta(days=-1)),
        (timedelta(seconds=1), timedelta()),
        (timedelta(), timedelta(seconds=-1)),
        (timedelta(), timedelta(hours=-1)),
    ],
)
def test_live_bumpa_rejects_shifted_or_clipped_timestamp_windows(
    start_delta: timedelta,
    end_delta: timedelta,
) -> None:
    requested_from = date(2026, 4, 2)
    requested_to = date(2026, 4, 15)
    expected = _inclusive_local_day_range(requested_from, requested_to, "Africa/Lagos")
    shifted = {
        "from": (datetime.fromisoformat(expected["from"]) + start_delta).isoformat(),
        "to": (datetime.fromisoformat(expected["to"]) + end_delta).isoformat(),
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=_empty_orders())
        response = _analytics_response(request)
        payload = response.json()
        if "range" in payload:
            _set_analytics_range(payload, shifted)
        return httpx.Response(200, json=payload)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(requested_from, requested_to)

    assert len([dataset for dataset in result.datasets if dataset.availability == "error"]) == 8


def test_live_bumpa_sync_rejects_date_only_ranges_from_the_timestamp_contract() -> None:
    requested_from = date(2026, 4, 2)
    requested_to = date(2026, 4, 15)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=_empty_orders())
        response = _analytics_response(request)
        payload = response.json()
        if "range" in payload:
            _set_analytics_range(
                payload,
                {"from": requested_from.isoformat(), "to": requested_to.isoformat()},
            )
        return httpx.Response(200, json=payload)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(requested_from, requested_to)

    assert len([dataset for dataset in result.datasets if dataset.availability == "error"]) == 8


def test_live_bumpa_verify_enforces_the_same_exact_timestamp_window() -> None:
    def provider(*, shift_start: timedelta) -> BumpaClient:
        def respond(request: httpx.Request) -> httpx.Response:
            date_from = date.fromisoformat(request.url.params["from"])
            date_to = date.fromisoformat(request.url.params["to"])
            response = _analytics_response(request)
            payload = response.json()
            expected = _inclusive_local_day_range(date_from, date_to, "Africa/Lagos")
            expected["from"] = (datetime.fromisoformat(expected["from"]) + shift_start).isoformat()
            _set_analytics_range(payload, expected)
            return httpx.Response(200, json=payload)

        return BumpaClient(
            "secret",
            "business_id",
            "business-test",
            client=_client(httpx.MockTransport(respond)),
            sleep=lambda _seconds: None,
        )

    provider(shift_start=timedelta()).verify()
    with pytest.raises(BumpaProviderError, match="range did not match"):
        provider(shift_start=timedelta(seconds=1)).verify()


def test_live_bumpa_retries_rate_limits_and_bounds_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "slow down"}, headers={"Retry-After": "90"})
        return _analytics_response(_request)

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
    assert status == 200 and payload["data"]
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
    ("failure_mode", "expected_status", "expected_kind"),
    [
        ("gateway", 504, "upstream_http"),
        ("timeout", None, "timeout"),
        ("protocol", None, "transport"),
    ],
)
def test_live_bumpa_sync_persists_one_isolated_exhausted_dataset_failure(
    failure_mode: str,
    expected_status: int | None,
    expected_kind: str,
) -> None:
    private_detail = "private-upstream-detail-must-not-be-persisted"
    calls: dict[str, int] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            calls["orders"] = calls.get("orders", 0) + 1
            return httpx.Response(
                200,
                json=_empty_orders(),
            )
        key = f"{request.url.path.rsplit('/', 1)[-1]}.{request.url.params['dataset']}"
        calls[key] = calls.get(key, 0) + 1
        if key == "products.products.overview":  # pragma: no cover - defensive typo guard
            raise AssertionError("unexpected dataset key")
        if key == "products.overview":
            if failure_mode == "timeout":
                raise httpx.ReadTimeout(private_detail, request=request)
            if failure_mode == "protocol":
                raise httpx.RemoteProtocolError(private_detail, request=request)
            return httpx.Response(504, json={"error": private_detail})
        return _analytics_response(request)

    provider = BumpaClient(
        "private-api-key",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
        max_attempts=3,
    )
    result = provider.sync(date(2026, 1, 1), date(2026, 1, 31))

    failed = next(
        dataset
        for dataset in result.datasets
        if dataset.resource == "products" and dataset.dataset == "overview"
    )
    evidence = next(
        response
        for response in result.responses
        if response.resource == "products" and response.dataset == "overview"
    )
    assert len(result.datasets) == 10
    assert failed.availability == "error"
    assert failed.value is None
    assert failed.payload == {}
    assert evidence.status_code == expected_status
    assert evidence.failure_kind == expected_kind
    assert evidence.payload == {}
    assert calls["products.overview"] == 3
    assert calls["products.products_sold"] == 1
    assert calls["customers.top_customers_order"] == 1
    assert calls["orders"] == 1
    assert private_detail not in repr(result)
    assert "private-api-key" not in repr(result)


def test_live_bumpa_sync_isolates_multiple_endpoint_failures_and_still_fetches_orders() -> None:
    calls: dict[str, int] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            calls["orders"] = calls.get("orders", 0) + 1
            return httpx.Response(200, json=_empty_orders())
        key = f"{request.url.path.rsplit('/', 1)[-1]}.{request.url.params['dataset']}"
        calls[key] = calls.get(key, 0) + 1
        if key in {"products.overview", "products.products_sold"}:
            return httpx.Response(503, json={"error": "private outage detail"})
        return _analytics_response(request)

    provider = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    result = provider.sync(date(2026, 1, 1), date(2026, 1, 31))

    failed = {
        f"{dataset.resource}.{dataset.dataset}"
        for dataset in result.datasets
        if dataset.availability == "error"
    }
    assert len(result.datasets) == 10
    assert failed == {"products.overview", "products.products_sold"}
    assert calls["products.overview"] == 2
    assert calls["products.products_sold"] == 1
    assert calls["products.top_selling_products"] == 1
    assert calls["customers.top_customers_order"] == 1
    assert calls["orders"] == 1
    assert sum(calls.values()) == 12
    assert result.orders_availability == "available"


def test_post_request_analytics_decode_failure_preserves_received_evidence() -> None:
    marker = "received-invalid-analytics-evidence"
    request_id = "analytics-invalid-shape-request"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=_empty_orders())
        response = _analytics_response(request)
        payload = response.json()
        area = request.url.path.rsplit("/", 1)[-1]
        dataset = request.url.params["dataset"]
        if (area, dataset) == ("products", "top_selling_products"):
            payload["data"][0]["data"][0]["label"] = ""
            payload["evidence_marker"] = marker
            return httpx.Response(200, json=payload, headers={"x-request-id": request_id})
        return httpx.Response(200, json=payload)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    failed = next(
        dataset
        for dataset in result.datasets
        if (dataset.resource, dataset.dataset) == ("products", "top_selling_products")
    )
    evidence = next(
        response
        for response in result.responses
        if (response.resource, response.dataset) == ("products", "top_selling_products")
    )
    expected_request_id_hash = hashlib.sha256(request_id.encode()).hexdigest()
    assert failed.availability == "error"
    assert failed.payload["evidence_marker"] == marker
    assert evidence.status_code == 200
    assert evidence.failure_kind == "invalid_response"
    assert evidence.payload["evidence_marker"] == marker
    assert evidence.headers == {"x-request-id-sha256": expected_request_id_hash}
    assert evidence.request_id_hash == expected_request_id_hash


def test_post_request_order_decode_failure_preserves_received_page_evidence() -> None:
    marker = "received-invalid-order-evidence"
    request_id = "orders-invalid-money-request"

    def respond(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/orders"):
            return _analytics_response(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "evidence_marker": marker,
                "orders": {
                    "data": [{"id": "invalid-money", "total": "$12.50"}],
                    "current_page": 1,
                    "last_page": 1,
                    "per_page": 100,
                    "total": 1,
                },
            },
            headers={"x-request-id": request_id},
        )

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    evidence = next(response for response in result.responses if response.resource == "orders")
    expected_request_id_hash = hashlib.sha256(request_id.encode()).hexdigest()
    assert result.orders == []
    assert result.orders_availability == "error"
    assert evidence.status_code == 200
    assert evidence.failure_kind == "invalid_response"
    assert evidence.payload["evidence_marker"] == marker
    assert evidence.headers == {"x-request-id-sha256": expected_request_id_hash}
    assert evidence.request_id_hash == expected_request_id_hash


def test_leading_dataset_failures_cannot_hide_later_analytics_or_orders() -> None:
    calls: dict[str, int] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            calls["orders"] = calls.get("orders", 0) + 1
            return httpx.Response(200, json=_empty_orders())
        key = f"{request.url.path.rsplit('/', 1)[-1]}.{request.url.params['dataset']}"
        calls[key] = calls.get(key, 0) + 1
        if key == "sales.overview":
            raise httpx.ReadTimeout("private timeout", request=request)
        if key == "sales.total_sales":
            return httpx.Response(503, json={"error": "private outage detail"})
        return _analytics_response(request)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
        max_attempts=2,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    failures = {
        f"{dataset.resource}.{dataset.dataset}"
        for dataset in result.datasets
        if dataset.availability == "error"
    }
    assert failures == {"sales.overview", "sales.total_sales"}
    assert len(result.datasets) == 10
    assert result.orders_availability == "available"
    assert calls == {
        "sales.overview": 2,
        "sales.total_sales": 1,
        "sales.gross_profit": 1,
        "sales.net_profit": 1,
        "products.overview": 1,
        "products.products_sold": 1,
        "products.top_selling_products": 1,
        "products.least_selling_products": 1,
        "customers.overview": 1,
        "customers.top_customers_order": 1,
        "orders": 1,
    }


@pytest.mark.parametrize(
    ("status_code", "failure_kind"),
    [(401, "authentication"), (429, "rate_limited")],
)
def test_live_bumpa_sync_never_degrades_global_auth_or_rate_limit_failures(
    status_code: int, failure_kind: str
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/orders"):
            return httpx.Response(
                200,
                json=_empty_orders(),
            )
        if (
            request.url.path.endswith("/products")
            and request.url.params.get("dataset") == "overview"
        ):
            return httpx.Response(status_code, json={"error": "private provider detail"})
        return _analytics_response(request)

    provider = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    with pytest.raises(BumpaProviderError) as raised:
        provider.sync(date(2026, 1, 1), date(2026, 1, 31))

    assert raised.value.failure_kind == failure_kind
    assert raised.value.retryable is (status_code == 429)
    # Four successful sales datasets precede the failing products overview.
    assert calls == (5 if status_code == 401 else 6)


@pytest.mark.parametrize(
    ("failed_area", "failed_dataset"),
    [
        ("sales", "overview"),
        ("products", "overview"),
        ("customers", "top_customers_order"),
    ],
)
def test_isolated_dataset_degradation_is_independent_of_dataset_order(
    failed_area: str, failed_dataset: str
) -> None:
    calls: dict[str, int] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            calls["orders"] = calls.get("orders", 0) + 1
            return httpx.Response(
                200,
                json=_empty_orders(),
            )
        area = request.url.path.rsplit("/", 1)[-1]
        dataset = request.url.params["dataset"]
        key = f"{area}.{dataset}"
        calls[key] = calls.get(key, 0) + 1
        if (area, dataset) == (failed_area, failed_dataset):
            return httpx.Response(504, json={"error": "private gateway detail"})
        return _analytics_response(request)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
        max_attempts=2,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    failed = [dataset for dataset in result.datasets if dataset.availability == "error"]
    assert [(dataset.resource, dataset.dataset) for dataset in failed] == [
        (failed_area, failed_dataset)
    ]
    assert len(result.datasets) == 10
    assert calls[f"{failed_area}.{failed_dataset}"] == 2
    assert calls["orders"] == 1


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
    raw_request_id = "bumpa-request-A1B2C3D4"
    calls = 0

    def unavailable(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            json={"error": f"upstream detail containing {secret}"},
            headers={"Retry-After": "0", "X-Request-ID": raw_request_id},
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
    assert raised.value.request_id_hash == hashlib.sha256(raw_request_id.encode()).hexdigest()
    assert raised.value.retry_after_seconds == 0
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "request_id",
    [
        "123456",
        "2348000000000",
        "+2348000000000",
        "request-2348000000000",
        "short",
        "request-id-with-newline\nprivate",
    ],
)
def test_live_bumpa_drops_sensitive_or_malformed_provider_request_ids(
    request_id: str,
) -> None:
    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": "private provider detail"},
            headers={"X-Request-ID": request_id},
        )

    provider = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(unavailable)),
        sleep=lambda _seconds: None,
        max_attempts=1,
    )

    with pytest.raises(BumpaProviderError) as raised:
        provider.get_analytics("sales", "overview", date(2026, 1, 1), date(2026, 1, 1))

    assert raised.value.request_id_hash is None


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
            return httpx.Response(200, json=_empty_orders())
        if request.url.params["dataset"] == "least_selling_products":
            return httpx.Response(200, json={"error": "Dataset is not available for this shop"})
        return _analytics_response(request)

    provider = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    )

    result = provider.sync(date(2026, 1, 1), date(2026, 1, 1))

    unavailable = [dataset for dataset in result.datasets if dataset.availability == "unavailable"]
    assert {dataset.dataset for dataset in unavailable} == {
        "gross_profit",
        "net_profit",
        "least_selling_products",
    }
    assert all(dataset.value is None for dataset in unavailable)
    assert result.orders_availability == "available"


def test_live_bumpa_preserves_nonretryable_orders_failure_as_partial_state() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            return httpx.Response(422, json={"error": "Orders scope is unavailable"})
        return _analytics_response(request)

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
    assert result.orders_error == "Bumpa request failed with HTTP 422"


def test_live_bumpa_marks_paginated_orders_partial_when_a_later_page_fails() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/orders"):
            return _analytics_response(request)
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "orders": {
                        "data": [{"id": "page-one-order", "status": "paid", "total": "5"}],
                        "current_page": 1,
                        "last_page": 2,
                        "per_page": 1,
                        "total": 2,
                    },
                },
            )
        return httpx.Response(422, json={"error": "Later orders page is unavailable"})

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    # A failed later page invalidates the entire order extraction. Page-one data
    # remains response evidence only and must never replace the canonical order set.
    assert result.orders == []
    assert result.orders_availability == "error"
    assert result.orders_error == "Bumpa request failed with HTTP 422"
    assert result.responses[-1].failure_kind == "upstream_http"


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
    order = _normalise_order(
        {"id": "order-without-money", "status": "pending"}, store_currency="NGN"
    )

    assert order.total_amount is None


@pytest.mark.parametrize("invalid_value", [None, True, "", "not-money", "$12.50"])
def test_live_bumpa_rejects_present_unparseable_order_money(invalid_value: object) -> None:
    with pytest.raises(BumpaProviderError) as raised:
        _normalise_order(
            {"id": "order-with-invalid-money", "total": invalid_value},
            store_currency="NGN",
        )

    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "invalid-secondary", "total_amount": "12", "total": "$12"},
        {"id": "conflicting-aliases", "total_amount": "12", "total": "13"},
    ],
)
def test_live_bumpa_rejects_invalid_or_conflicting_present_total_aliases(
    payload: dict[str, object],
) -> None:
    with pytest.raises(BumpaProviderError) as raised:
        _normalise_order(payload, store_currency="NGN")

    assert raised.value.failure_kind == "invalid_response"


def test_live_bumpa_keeps_total_and_grand_total_as_distinct_order_facts() -> None:
    order = _normalise_order(
        {
            "id": "distinct-live-total-fields",
            "currency_code": "NGN",
            "total": "125",
            "grand_total": "25",
        },
        store_currency="NGN",
    )

    assert order.total_amount == 125


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "bad-shipping", "total": "12", "shipping_price": "not-money"},
        {"id": "conflicting-tax", "total": "12", "tax": "1", "tax_amount": "2"},
        {"id": "bad-items-container", "total": "12", "items": {"price": "2"}},
        {"id": "bad-item-row", "total": "12", "items": ["not-an-item"]},
        {
            "id": "bad-item-money",
            "total": "12",
            "items": [{"quantity": "1", "price": "not-money"}],
        },
        {
            "id": "negative-item-quantity",
            "total": "12",
            "items": [{"quantity": "-1", "price": "2"}],
        },
        {
            "id": "conflicting-item-money",
            "total": "12",
            "items": [{"price": "2", "unit_price": "3"}],
        },
    ],
)
def test_live_bumpa_rejects_malformed_or_conflicting_ancillary_order_facts(
    payload: dict[str, object],
) -> None:
    with pytest.raises(BumpaProviderError) as raised:
        _normalise_order(payload, store_currency="NGN")

    assert raised.value.failure_kind == "invalid_response"


def test_documented_analytics_shapes_are_canonicalized_without_fake_scalars() -> None:
    decoded = {
        key: decode_analytics_dataset(*key.split(".", 1), 200, deepcopy(payload))
        for key, payload in ANALYTICS.items()
    }

    assert decoded["sales.total_sales"].value == 12500.50
    assert decoded["sales.total_sales"].currency_code == "NGN"
    assert decoded["sales.total_sales"].response_from.isoformat() == "2026-01-01T00:00:00+00:00"
    assert decoded["sales.total_sales"].response_to.isoformat() == "2026-01-31T00:00:00+00:00"
    assert decoded["products.products_sold"].value == 7
    assert decoded["products.products_sold"].currency_code is None
    assert decoded["products.top_selling_products"].value is None
    assert decoded["products.top_selling_products"].canonical_payload["groups"][0]["rows"] == [
        {"id": "101", "label": "Synthetic Best Seller", "rank": "1", "value": "5"}
    ]
    assert decoded["customers.top_customers_order"].canonical_payload["groups"][0]["rows"] == [
        {"label": "Customer 1", "rank": "1", "value": "3"}
    ]
    assert (
        decoded["customers.top_customers_order"].canonical_payload["groups"][0]["title"]
        == "Top customers"
    )
    assert decoded["sales.gross_profit"].availability == "unavailable"
    assert decoded["sales.gross_profit"].value is None


def test_customer_rankings_redact_nested_case_variant_identity_fields_and_titles() -> None:
    payload = deepcopy(ANALYTICS["customers.top_customers_order"])
    summary = payload["data"]["summary"]
    summary["title"] = "Private Customer Name"
    summary["data"][0].update(
        {
            "FIRST-NAME": "Private",
            "profile": {
                "Display Name": "Private Customer Name",
                "LAST_name": "Customer Name",
            },
        }
    )

    canonical = decode_analytics_dataset(
        "customers", "top_customers_order", 200, payload
    ).canonical_payload
    evidence = redact_bumpa_payload(payload, resource="customers", dataset="top_customers_order")

    assert canonical["groups"][0]["title"] == "Top customers"
    assert canonical["groups"][0]["rows"][0]["label"] == "Customer 1"
    serialized = json.dumps(evidence)
    assert "Private" not in serialized
    assert "Customer Name" not in serialized
    assert serialized.count("[REDACTED]") >= 6


def test_customer_rankings_preserve_anonymous_aggregate_rows_without_identity_data() -> None:
    payload = deepcopy(ANALYTICS["customers.top_customers_order"])
    anonymous = payload["data"]["summary"]["data"][0]
    anonymous["id"] = ""
    anonymous["label"] = ""

    decoded = decode_analytics_dataset("customers", "top_customers_order", 200, payload)

    assert decoded.availability == "available"
    assert decoded.canonical_payload["groups"][0]["rows"][0] == {
        "label": "Customer 1",
        "rank": "1",
        "value": "3",
    }


@pytest.mark.parametrize(
    "invalid_row",
    [
        {"id": "", "label": "", "value": -1},
        {"id": "", "label": "", "value": "1.5"},
        {"id": "", "label": "", "sales": 4},
        {"id": "", "label": "", "quantity": 4},
    ],
)
def test_customer_ranking_order_counts_reject_non_counts(invalid_row: dict[str, object]) -> None:
    payload = deepcopy(ANALYTICS["customers.top_customers_order"])
    payload["data"]["summary"]["data"] = [invalid_row]

    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("customers", "top_customers_order", 200, payload)

    assert raised.value.failure_kind == "invalid_response"


def test_product_rankings_still_reject_rows_without_a_product_label() -> None:
    payload = deepcopy(ANALYTICS["products.top_selling_products"])
    payload["data"][0]["data"][0]["label"] = ""

    with pytest.raises(BumpaProviderError):
        decode_analytics_dataset("products", "top_selling_products", 200, payload)


@pytest.mark.parametrize("identity_alias", ["customer_name", "customer"])
def test_product_rankings_never_promote_customer_identity_as_product_label(
    identity_alias: str,
) -> None:
    payload = deepcopy(ANALYTICS["products.top_selling_products"])
    row = payload["data"][0]["data"][0]
    row["label"] = ""
    row[identity_alias] = "Private Customer Name"

    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("products", "top_selling_products", 200, payload)

    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize("invalid_value", [-1, "1.5"])
@pytest.mark.parametrize(
    ("dataset_key", "path"),
    [
        ("customers.overview", ("data", 0, "value")),
        ("products.overview", ("data", "total_products", "value")),
        ("products.products_sold", ("data", "summary", "value")),
        (
            "products.products_sold",
            ("data", "chart", "current_period", "data", 0, "value"),
        ),
        (
            "products.overview",
            ("data", "total_products_chart", "data", 0, "value"),
        ),
        ("products.top_selling_products", ("data", 0, "data", 0, "value")),
    ],
)
def test_every_canonical_count_domain_rejects_negative_or_fractional_values(
    dataset_key: str,
    path: tuple[str | int, ...],
    invalid_value: object,
) -> None:
    payload = deepcopy(ANALYTICS[dataset_key])
    target: Any = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid_value

    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset(*dataset_key.split(".", 1), 200, payload)

    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize(
    ("configured", "payload_currency"),
    [
        ("NGN", "USD"),
        ("GBP", "NGN"),
        ("NGN", "US"),
        ("NGN", 123),
    ],
)
def test_monetary_analytics_fail_closed_on_currency_conflict_or_malformed_claim(
    configured: str, payload_currency: object
) -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["currency_code"] = payload_currency

    with pytest.raises(BumpaProviderError) as raised:
        production_decode_analytics_dataset(
            "sales",
            "total_sales",
            200,
            payload,
            store_timezone="Africa/Lagos",
            store_currency=configured,
        )

    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize(
    ("currency", "symbol", "expected"),
    [("NGN", "₦", "12500.50"), ("GBP", "£", "12500.50"), ("KES", "KSh", "12500.50")],
)
def test_configured_currency_parses_matching_symbol_bearing_money(
    currency: str, symbol: str, expected: str
) -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload = json.loads(json.dumps(payload, ensure_ascii=False).replace("₦", symbol))
    payload["currency_code"] = currency.lower()

    decoded = production_decode_analytics_dataset(
        "sales",
        "total_sales",
        200,
        payload,
        store_timezone="Africa/Lagos",
        store_currency=currency,
    )

    assert str(decoded.value) == expected
    assert decoded.currency_code == currency


def test_order_currency_falls_back_to_store_context_and_accepts_explicit_order_currency() -> None:
    order = _normalise_order(
        {"id": "order-1", "status": "paid", "total": "£12.50"},
        store_currency="GBP",
    )
    assert order.currency_code == "GBP"
    assert order.total_amount == 12.50

    usd_order = _normalise_order(
        {"id": "order-2", "currency": "USD", "total": "$12.50"},
        store_currency="GBP",
    )
    assert usd_order.currency_code == "USD"
    assert usd_order.total_amount == 12.50

    with pytest.raises(BumpaProviderError) as raised:
        _normalise_order(
            {
                "id": "order-3",
                "currency": "USD",
                "currency_code": "GBP",
                "total": "12.50",
            },
            store_currency="GBP",
        )
    assert raised.value.failure_kind == "invalid_response"


def test_order_currency_validates_top_level_and_nested_item_claims_before_money() -> None:
    with pytest.raises(BumpaProviderError) as raised:
        _normalise_order(
            {
                "id": "mixed-currency-order",
                "currency": "GBP",
                "total": "not-money",
                "items": [
                    {
                        "currency_code": "USD",
                        "price": "$12.50",
                        "quantity": 1,
                    }
                ],
            },
            store_currency="GBP",
        )

    assert raised.value.failure_kind == "invalid_response"
    assert "currency" in str(raised.value)


def test_order_currency_accepts_consistent_nested_claims_and_historical_currency() -> None:
    explicit_gbp = _normalise_order(
        {
            "id": "consistent-gbp-order",
            "currency_code": "gbp",
            "total": "£12.50",
            "items": [
                {
                    "currency": "GBP",
                    "product": {"currency_code": "gbp"},
                    "price": "£12.50",
                    "quantity": 1,
                }
            ],
        },
        store_currency="NGN",
    )
    historical_usd = _normalise_order(
        {
            "id": "historical-usd-order",
            "total": "$8",
            "order_items": [{"currency": "USD", "price": "$8", "quantity": 1}],
        },
        store_currency="NGN",
    )

    assert explicit_gbp.currency_code == "GBP"
    assert explicit_gbp.total_amount == 12.50
    assert historical_usd.currency_code == "USD"
    assert historical_usd.total_amount == 8


def test_unknown_successful_analytics_shape_fails_closed_as_invalid_response() -> None:
    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("sales", "total_sales", 200, {"data": {"value": "4"}})

    assert raised.value.failure_kind == "invalid_response"
    assert raised.value.status_code == 200


@pytest.mark.parametrize("invalid_value", ["NaN", "Infinity", "-Infinity", "1E+25", "0.0000001"])
def test_non_finite_or_unpersistable_metrics_fail_schema_validation(invalid_value: str) -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["data"]["summary"]["value"] = invalid_value
    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("sales", "total_sales", 200, payload)
    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize(
    "invalid_range",
    [
        {"from": "not-a-date", "to": "2026-01-31"},
        {"from": "2026-02-01", "to": "2026-01-31"},
    ],
)
def test_invalid_or_inverted_response_ranges_fail_schema_validation(
    invalid_range: dict[str, str],
) -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["range"] = invalid_range
    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("sales", "total_sales", 200, payload)
    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["range"].update(
            {"from": "2026-01-01T00:00:00", "to": "2026-01-31T23:59:59"}
        ),
        lambda payload: payload["data"]["chart"]["current_period"].update(
            {"range": ["2026-01-01T00:00:00", "2026-01-31T23:59:59"]}
        ),
    ],
)
def test_time_bearing_ranges_without_an_explicit_offset_fail_closed(mutate: Any) -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    mutate(payload)

    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("sales", "total_sales", 200, payload)

    assert raised.value.failure_kind == "invalid_response"


def test_current_chart_period_must_match_the_top_level_provider_range() -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["data"]["chart"]["current_period"]["range"][0] = "2025-12-31"

    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("sales", "total_sales", 200, payload)

    assert raised.value.failure_kind == "invalid_response"


def test_timestamp_parent_accepts_semantically_exact_date_only_nested_periods() -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["range"] = {
        "from": "2026-04-01T23:00:00.000000Z",
        "to": "2026-04-15T22:59:59.999999Z",
    }
    payload["data"]["chart"]["current_period"]["range"] = [
        "2026-04-02",
        "2026-04-15",
    ]
    payload["data"]["chart"]["previous_period"]["range"] = [
        "2026-03-19",
        "2026-04-01",
    ]

    decoded = production_decode_analytics_dataset(
        "sales",
        "total_sales",
        200,
        payload,
        store_timezone="Africa/Lagos",
        store_currency="NGN",
    )

    assert decoded.canonical_payload["series"]["current_period"]["range"] == [
        "2026-04-02",
        "2026-04-15",
    ]
    assert decoded.canonical_payload["series"]["previous_period"]["range"] == [
        "2026-03-19",
        "2026-04-01",
    ]


@pytest.mark.parametrize(
    ("area", "dataset"),
    [("sales", "total_sales"), ("products", "products_sold")],
)
def test_live_thirty_day_window_accepts_adjacent_thirty_one_day_comparison(
    area: str,
    dataset: str,
) -> None:
    payload = deepcopy(ANALYTICS[f"{area}.{dataset}"])
    payload["range"] = {
        "from": "2026-06-15T23:00:00.000000Z",
        "to": "2026-07-15T22:59:59.999999Z",
    }
    payload["data"]["chart"]["current_period"]["range"] = list(payload["range"].values())
    payload["data"]["chart"]["previous_period"]["range"] = [
        "2026-05-15T23:00:00.000000Z",
        "2026-06-15T22:59:59.999999Z",
    ]

    decoded = production_decode_analytics_dataset(
        area,
        dataset,
        200,
        payload,
        store_timezone="Africa/Lagos",
        store_currency="NGN",
    )

    assert decoded.canonical_payload["series"]["previous_period"]["range"] == [
        "2026-05-16",
        "2026-06-15",
    ]


@pytest.mark.parametrize(
    "previous_range",
    [
        ["2026-05-14T23:00:00.000000Z", "2026-06-14T22:59:59.999999Z"],
        ["2026-05-15T23:00:00.000000Z", "2026-06-16T22:59:59.999999Z"],
        ["2026-01-01T00:00:00.000000Z", "2026-06-15T22:59:59.999999Z"],
        ["2026-05-15T23:00:01.000000Z", "2026-06-15T22:59:59.999999Z"],
    ],
)
def test_previous_comparison_rejects_gap_overlap_unbounded_or_clipped_ranges(
    previous_range: list[str],
) -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["range"] = {
        "from": "2026-06-15T23:00:00.000000Z",
        "to": "2026-07-15T22:59:59.999999Z",
    }
    payload["data"]["chart"]["current_period"]["range"] = list(payload["range"].values())
    payload["data"]["chart"]["previous_period"]["range"] = previous_range

    with pytest.raises(BumpaProviderError) as raised:
        production_decode_analytics_dataset(
            "sales",
            "total_sales",
            200,
            payload,
            store_timezone="Africa/Lagos",
            store_currency="NGN",
        )

    assert raised.value.failure_kind == "invalid_response"


def test_timestamp_nested_period_still_requires_exact_provider_instants() -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["range"] = {
        "from": "2026-04-01T23:00:00.000000Z",
        "to": "2026-04-15T22:59:59.999999Z",
    }
    payload["data"]["chart"]["current_period"]["range"] = [
        "2026-04-01T23:00:01.000000Z",
        "2026-04-15T22:59:59.999999Z",
    ]
    payload["data"]["chart"]["previous_period"]["range"] = [
        "2026-03-18T23:00:00.000000Z",
        "2026-04-01T22:59:59.999999Z",
    ]

    with pytest.raises(BumpaProviderError) as raised:
        production_decode_analytics_dataset(
            "sales",
            "total_sales",
            200,
            payload,
            store_timezone="Africa/Lagos",
            store_currency="NGN",
        )

    assert raised.value.failure_kind == "invalid_response"


def test_previous_chart_period_still_requires_a_valid_ordered_range() -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["data"]["chart"]["previous_period"]["range"] = [
        "2026-01-01",
        "2025-12-01",
    ]

    with pytest.raises(BumpaProviderError):
        decode_analytics_dataset("sales", "total_sales", 200, payload)


def test_previous_chart_period_requires_exact_full_local_day_instants() -> None:
    payload = deepcopy(ANALYTICS["sales.total_sales"])
    payload["range"] = {
        "from": "2026-01-01T00:00:00.000000Z",
        "to": "2026-01-31T23:59:59.999999Z",
    }
    payload["data"]["chart"]["current_period"]["range"] = list(payload["range"].values())
    payload["data"]["chart"]["previous_period"]["range"] = [
        "2025-12-01T12:00:00.000000Z",
        "2025-12-31T12:00:00.000000Z",
    ]

    with pytest.raises(BumpaProviderError) as raised:
        production_decode_analytics_dataset(
            "sales",
            "total_sales",
            200,
            payload,
            store_timezone="UTC",
            store_currency="NGN",
        )

    assert raised.value.failure_kind == "invalid_response"


@pytest.mark.parametrize("chart_key", ["total_products_chart", "total_products_sold_chart"])
def test_products_overview_nested_period_must_match_top_level_range(chart_key: str) -> None:
    payload = deepcopy(ANALYTICS["products.overview"])
    payload["data"][chart_key]["range"][1] = "2026-01-30"

    with pytest.raises(BumpaProviderError) as raised:
        decode_analytics_dataset("products", "overview", 200, payload)

    assert raised.value.failure_kind == "invalid_response"


def test_client_rejects_invalid_store_context_before_any_provider_request() -> None:
    with pytest.raises(ValueError, match="IANA"):
        ProductionBumpaClient(
            "secret",
            "business_id",
            "business-test",
            store_timezone="Not/A_Zone",
            store_currency="NGN",
        )
    with pytest.raises(ValueError, match="three-letter"):
        ProductionBumpaClient(
            "secret",
            "business_id",
            "business-test",
            store_timezone="Africa/Lagos",
            store_currency="12!",
        )


def test_schema_valid_empty_rankings_are_preserved_as_meaningful_empty_results() -> None:
    payload = deepcopy(ANALYTICS["products.top_selling_products"])
    payload["data"] = []

    decoded = decode_analytics_dataset("products", "top_selling_products", 200, payload)

    assert decoded.availability == "available"
    assert decoded.value is None
    assert decoded.canonical_payload == {
        "schema_version": 1,
        "kind": "ranking",
        "range": {"from": "2026-01-01", "to": "2026-01-31"},
        "groups": [],
    }


def test_verification_distinguishes_ambiguous_empty_scope_from_valid_zero_metrics() -> None:
    def ambiguous(request: httpx.Request) -> httpx.Response:
        payload = deepcopy(ANALYTICS[f"{request.url.path.rsplit('/', 1)[-1]}.overview"])
        payload["data"] = []
        _set_analytics_range(
            payload,
            _inclusive_local_day_range(
                date.fromisoformat(request.url.params["from"]),
                date.fromisoformat(request.url.params["to"]),
                "Africa/Lagos",
            ),
        )
        return httpx.Response(200, json=payload)

    provider = BumpaClient(
        "secret",
        "business_id",
        "unknown-business",
        client=_client(httpx.MockTransport(ambiguous)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(BumpaProviderError) as raised:
        provider.verify()
    assert raised.value.failure_kind == "scope_ambiguous"

    def valid_zero(request: httpx.Request) -> httpx.Response:
        payload = deepcopy(ANALYTICS[f"{request.url.path.rsplit('/', 1)[-1]}.overview"])
        for item in payload["data"]:
            item["value"] = 0
        _set_analytics_range(
            payload,
            _inclusive_local_day_range(
                date.fromisoformat(request.url.params["from"]),
                date.fromisoformat(request.url.params["to"]),
                "Africa/Lagos",
            ),
        )
        return httpx.Response(200, json=payload)

    BumpaClient(
        "secret",
        "business_id",
        "new-business",
        client=_client(httpx.MockTransport(valid_zero)),
        sleep=lambda _seconds: None,
    ).verify()


def test_order_pagination_supports_more_than_ten_thousand_records() -> None:
    total = 10_001
    per_page = 100
    last_page = 101

    def respond(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/orders"):
            return _analytics_response(request)
        page = int(request.url.params["page"])
        start = (page - 1) * per_page
        rows = [
            {"id": f"order-{index}", "status": "paid", "total": "1.00"}
            for index in range(start, min(start + per_page, total))
        ]
        return httpx.Response(
            200,
            json={
                "success": True,
                "orders": {
                    "data": rows,
                    "current_page": page,
                    "last_page": last_page,
                    "per_page": per_page,
                    "total": total,
                },
            },
        )

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    assert len(result.orders) == total
    assert result.orders[-1].order_id == "order-10000"


def test_exact_page_overlap_is_deduplicated_but_cannot_hide_a_missing_order() -> None:
    def provider_for(second_page: list[dict[str, str]], total: int) -> BumpaClient:
        def respond(request: httpx.Request) -> httpx.Response:
            if not request.url.path.endswith("/orders"):
                return _analytics_response(request)
            page = int(request.url.params["page"])
            rows = [{"id": "order-1", "status": "paid", "total": "1"}]
            if page == 2:
                rows = second_page
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "orders": {
                        "data": rows,
                        "current_page": page,
                        "last_page": 2,
                        "per_page": 2,
                        "total": total,
                    },
                },
            )

        return BumpaClient(
            "secret",
            "business_id",
            "business-test",
            client=_client(httpx.MockTransport(respond)),
            sleep=lambda _seconds: None,
        )

    exact = {"id": "order-1", "status": "paid", "total": "1"}
    second = {"id": "order-2", "status": "paid", "total": "2"}
    accepted = provider_for([exact, second], 2).sync(date(2026, 1, 1), date(2026, 1, 2))
    assert [order.order_id for order in accepted.orders] == ["order-1", "order-2"]

    incomplete = provider_for([exact], 2).sync(date(2026, 1, 1), date(2026, 1, 2))
    assert incomplete.orders == []
    assert incomplete.orders_availability == "error"
    assert incomplete.responses[-1].failure_kind == "invalid_response"
    assert "incomplete" in (incomplete.orders_error or "")

    conflicting = {"id": "order-1", "status": "refunded", "total": "1"}
    conflict = provider_for([conflicting, second], 2).sync(date(2026, 1, 1), date(2026, 1, 2))
    assert conflict.orders == []
    assert conflict.responses[-1].failure_kind == "invalid_response"
    assert "conflicting duplicate" in (conflict.orders_error or "")


def test_orders_transport_failure_preserves_strictly_decoded_metric_domains() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders"):
            raise httpx.ReadTimeout("private order timeout", request=request)
        return _analytics_response(request)

    result = BumpaClient(
        "secret",
        "business_id",
        "business-test",
        client=_client(httpx.MockTransport(respond)),
        sleep=lambda _seconds: None,
        max_attempts=1,
    ).sync(date(2026, 1, 1), date(2026, 1, 31))

    assert len(result.datasets) == 10
    assert result.orders == []
    assert result.orders_availability == "error"
    assert result.responses[-1].failure_kind == "timeout"
    assert "private order timeout" not in repr(result)
