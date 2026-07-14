from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx

from app.providers.contracts import ProviderDataset, ProviderOrder
from app.providers.diagnostics import provider_request_id_hash
from app.providers.redaction import parse_money

BUMPA_BASE_URL = "https://api.getbumpa.com/api"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ORDER_PAGES = 1_000
MAX_ORDER_RECORDS = 100_000
MAX_ANALYTICS_POINTS = 2_000
MAX_RANKING_ROWS = 500
DATASETS: dict[str, tuple[str, ...]] = {
    "sales": ("overview", "total_sales", "gross_profit", "net_profit"),
    "products": (
        "overview",
        "products_sold",
        "top_selling_products",
        "least_selling_products",
    ),
    "customers": ("overview", "top_customers_order"),
}
KNOWN_UNAVAILABLE_MESSAGES = frozenset(
    {
        "Gross profit cannot be calculated for this store",
        "Net profit cannot be calculated for this store",
    }
)


BumpaFailureKind = Literal[
    "timeout",
    "transport",
    "rate_limited",
    "authentication",
    "scope_ambiguous",
    "provider",
    "invalid_response",
    "response_too_large",
]
DatasetFailureKind = Literal["timeout", "transport", "upstream_http", "invalid_response"]


class BumpaProviderError(RuntimeError):
    """Sanitized upstream error that never contains credentials or response bodies."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        failure_kind: BumpaFailureKind = "provider",
        request_id_hash: str | None = None,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.failure_kind = failure_kind
        self.request_id_hash = request_id_hash
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class BumpaResponse:
    resource: str
    dataset: str | None
    status_code: int | None
    payload: dict[str, Any]
    headers: dict[str, str]
    availability: str
    error: str | None
    failure_kind: DatasetFailureKind | None = None
    retryable: bool = False
    request_id_hash: str | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class BumpaSyncResult:
    datasets: list[ProviderDataset]
    orders: list[ProviderOrder]
    responses: list[BumpaResponse]
    rate_limit_limit: int | None
    rate_limit_remaining: int | None
    orders_availability: str
    orders_error: str | None


class BumpaClient:
    """Read-only direct Bumpa REST client with bounded retries and payload sizes."""

    def __init__(
        self,
        api_key: str,
        scope_type: str,
        scope_id: str,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        if scope_type not in {"business_id", "location_id"}:
            raise ValueError("Invalid Bumpa scope_type")
        if not api_key.strip() or not scope_id.strip():
            raise ValueError("Bumpa credentials and scope are required")
        self._api_key = api_key
        self.scope_type = scope_type
        self.scope_id = scope_id
        self._client = client or httpx.Client(
            base_url=BUMPA_BASE_URL,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._sleep = sleep
        self._max_attempts = max(1, min(max_attempts, 4))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BumpaClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "BumpaBestie/1.0",
            "X-Api-Key": self._api_key,
        }

    @property
    def _scope(self) -> dict[str, str]:
        return {self.scope_type: self.scope_id}

    def _request(
        self,
        path: str,
        params: dict[str, str],
        *,
        max_attempts: int | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        attempt_limit = self._max_attempts if max_attempts is None else max_attempts
        if not 1 <= attempt_limit <= self._max_attempts:
            raise ValueError("Invalid Bumpa request attempt limit")
        for attempt in range(1, attempt_limit + 1):
            try:
                response = self._client.get(path, params=params, headers=self._headers)
            except httpx.TimeoutException as exc:
                if attempt == attempt_limit:
                    raise BumpaProviderError(
                        "Bumpa is temporarily unreachable",
                        retryable=True,
                        failure_kind="timeout",
                    ) from exc
                self._sleep(min(2 ** (attempt - 1), 4))
                continue
            except httpx.TransportError as exc:
                if attempt == attempt_limit:
                    raise BumpaProviderError(
                        "Bumpa is temporarily unreachable",
                        retryable=True,
                        failure_kind="transport",
                    ) from exc
                self._sleep(min(2 ** (attempt - 1), 4))
                continue

            request_id_hash = provider_request_id_hash(response.headers.get("x-request-id"))
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < attempt_limit:
                retry_after = _bounded_retry_after(response.headers.get("retry-after"))
                self._sleep(retry_after if retry_after is not None else min(2 ** (attempt - 1), 4))
                continue

            # Authentication and rate-limit failures invalidate or throttle the
            # entire connection. Other exhausted upstream failures are classified
            # by the caller at the endpoint boundary.
            if response.status_code in {401, 403}:
                raise BumpaProviderError(
                    "Bumpa authentication failed",
                    status_code=response.status_code,
                    retryable=False,
                    failure_kind="authentication",
                    request_id_hash=request_id_hash,
                )
            if retryable:
                message = (
                    "Bumpa rate limit was exhausted"
                    if response.status_code == 429
                    else "Bumpa is temporarily unavailable"
                )
                raise BumpaProviderError(
                    message,
                    status_code=response.status_code,
                    retryable=True,
                    failure_kind=("rate_limited" if response.status_code == 429 else "provider"),
                    request_id_hash=request_id_hash,
                    retry_after_seconds=_bounded_retry_after(response.headers.get("retry-after")),
                )

            content_length = response.headers.get("content-length")
            if (
                content_length
                and content_length.isdigit()
                and int(content_length) > MAX_RESPONSE_BYTES
            ):
                raise BumpaProviderError(
                    "Bumpa response exceeded the size limit",
                    status_code=response.status_code,
                    failure_kind="response_too_large",
                    request_id_hash=request_id_hash,
                )
            content = response.content
            if len(content) > MAX_RESPONSE_BYTES:
                raise BumpaProviderError(
                    "Bumpa response exceeded the size limit",
                    status_code=response.status_code,
                    failure_kind="response_too_large",
                    request_id_hash=request_id_hash,
                )
            try:
                decoded = response.json()
            except ValueError as exc:
                raise BumpaProviderError(
                    "Bumpa returned an invalid response",
                    status_code=response.status_code,
                    retryable=retryable,
                    failure_kind="invalid_response",
                    request_id_hash=request_id_hash,
                ) from exc
            if not isinstance(decoded, dict):
                raise BumpaProviderError(
                    "Bumpa returned an unexpected response",
                    status_code=response.status_code,
                    failure_kind="invalid_response",
                    request_id_hash=request_id_hash,
                )
            safe_headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"}
            }
            if request_id_hash is not None:
                safe_headers["x-request-id-sha256"] = request_id_hash
            return response.status_code, decoded, safe_headers
        raise AssertionError("Bumpa request attempts exhausted")

    def get_analytics(
        self,
        area: str,
        dataset: str,
        date_from: date,
        date_to: date,
        *,
        max_attempts: int | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        if area not in DATASETS or dataset not in DATASETS[area]:
            raise ValueError("Unsupported Bumpa analytics dataset")
        params = {
            "dataset": dataset,
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            **self._scope,
        }
        return self._request(f"/commerce/v1/analytics/{area}", params, max_attempts=max_attempts)

    def get_orders_page(
        self,
        date_from: date,
        date_to: date,
        page: int,
        *,
        limit: int = 100,
        max_attempts: int | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError("Invalid Bumpa pagination")
        params = {
            "from_date": date_from.isoformat(),
            "to_date": date_to.isoformat(),
            "page": str(page),
            "limit": str(limit),
            "orderBy": "desc",
            "orderByField": "created_at",
            **self._scope,
        }
        return self._request("/commerce/v1/orders", params, max_attempts=max_attempts)

    def _sync_orders(
        self,
        date_from: date,
        date_to: date,
        current_limit: int | None,
        current_remaining: int | None,
        *,
        max_attempts: int | None = None,
    ) -> tuple[
        list[ProviderOrder],
        list[BumpaResponse],
        str,
        str | None,
        int | None,
        int | None,
    ]:
        responses: list[BumpaResponse] = []
        orders: list[ProviderOrder] = []
        page = 1
        expected_total: int | None = None
        expected_last_page: int | None = None
        seen_orders: dict[str, ProviderOrder] = {}
        availability = "unavailable"
        error: str | None = "Orders were not requested"
        last_http_status: int | None = None
        try:
            while page <= MAX_ORDER_PAGES:
                status, payload, headers = self.get_orders_page(
                    date_from,
                    date_to,
                    page,
                    max_attempts=max_attempts,
                )
                last_http_status = status
                availability, error = classify_availability(status, payload)
                current_limit, current_remaining = _merge_rate_limits(
                    headers, current_limit, current_remaining
                )
                if availability != "available":
                    responses.append(
                        BumpaResponse(
                            "orders",
                            None,
                            status,
                            payload,
                            headers,
                            availability,
                            error,
                            "upstream_http" if availability == "error" else None,
                            status == 429 or status >= 500,
                            headers.get("x-request-id-sha256"),
                            _bounded_retry_after(headers.get("retry-after")),
                        )
                    )
                    orders.clear()
                    break
                raw_orders, current, last, total = _decode_orders_page(payload, page)
                if total > MAX_ORDER_RECORDS or last > MAX_ORDER_PAGES:
                    raise BumpaProviderError(
                        "Bumpa orders exceeded the synchronization safety limit",
                        status_code=status,
                        failure_kind="invalid_response",
                    )
                if expected_total is None:
                    expected_total, expected_last_page = total, last
                elif total != expected_total or last != expected_last_page:
                    raise BumpaProviderError(
                        "Bumpa orders pagination changed during synchronization",
                        status_code=status,
                        failure_kind="invalid_response",
                    )
                for row in raw_orders:
                    order = _normalise_order(row)
                    existing_order = seen_orders.get(order.order_id)
                    if existing_order is not None and existing_order != order:
                        raise BumpaProviderError(
                            "Bumpa orders pagination returned a conflicting duplicate order",
                            status_code=status,
                            failure_kind="invalid_response",
                        )
                    if existing_order is not None:
                        continue
                    seen_orders[order.order_id] = order
                    orders.append(order)
                responses.append(
                    BumpaResponse(
                        "orders",
                        None,
                        status,
                        payload,
                        headers,
                        availability,
                        error,
                        None,
                        False,
                        headers.get("x-request-id-sha256"),
                        _bounded_retry_after(headers.get("retry-after")),
                    )
                )
                if current >= last:
                    break
                if current != page or not raw_orders:
                    raise BumpaProviderError(
                        "Bumpa orders pagination did not advance",
                        status_code=status,
                        failure_kind="invalid_response",
                    )
                page = current + 1
            else:
                raise BumpaProviderError(
                    "Bumpa orders exceeded the pagination limit",
                    failure_kind="invalid_response",
                )

            if (
                availability == "available"
                and error is None
                and (expected_total is None or len(orders) != expected_total)
            ):
                raise BumpaProviderError(
                    "Bumpa orders response was incomplete", failure_kind="invalid_response"
                )
        except BumpaProviderError as exc:
            if exc.failure_kind in {"authentication", "rate_limited"}:
                raise
            orders.clear()
            availability, error = "error", str(exc)
            evidence_kind: DatasetFailureKind = (
                _dataset_failure_kind(exc)
                if exc.failure_kind in {"timeout", "transport", "provider"}
                else "invalid_response"
            )
            responses.append(
                BumpaResponse(
                    "orders",
                    None,
                    exc.status_code if exc.status_code is not None else last_http_status,
                    {},
                    {},
                    availability,
                    error,
                    evidence_kind,
                    exc.retryable,
                    exc.request_id_hash,
                    exc.retry_after_seconds,
                )
            )
        return (
            orders,
            responses,
            availability,
            error,
            current_limit,
            current_remaining,
        )

    def verify(self) -> None:
        # Bumpa can return HTTP 200 with an empty dataset for an unrelated scope.
        # Require two independent schema-valid, populated analytics contracts so
        # an ambiguous 200-empty response can never activate a connection. Zero
        # values are valid; missing metric rows are not.
        today = datetime.now(UTC).date()
        date_from = date(today.year - 1, today.month, min(today.day, 28))
        verified: list[ProviderDataset] = []
        scope_identified = False
        for area, dataset in (("sales", "overview"), ("customers", "overview")):
            status, payload, headers = self.get_analytics(area, dataset, date_from, today)
            decoded = decode_analytics_dataset(area, dataset, status, payload)
            if decoded.availability != "available":
                raise BumpaProviderError(
                    decoded.error or "Bumpa scope could not be verified",
                    status_code=status,
                    retryable=status == 429 or status >= 500,
                    failure_kind=("provider" if status >= 500 else "invalid_response"),
                    request_id_hash=headers.get("x-request-id-sha256"),
                    retry_after_seconds=_bounded_retry_after(headers.get("retry-after")),
                )
            verified.append(decoded)
            scope_identified = scope_identified or _payload_identifies_scope(
                payload, self.scope_type, self.scope_id
            )
        if len(verified) != 2:  # pragma: no cover - defensive completeness guard
            raise BumpaProviderError(
                "Bumpa scope could not be verified", failure_kind="invalid_response"
            )
        if not scope_identified and not any(dataset.value is not None for dataset in verified):
            raise BumpaProviderError(
                "Bumpa credentials were accepted but the scope returned no identifying data",
                failure_kind="scope_ambiguous",
            )

    def sync(self, date_from: date, date_to: date) -> BumpaSyncResult:
        responses: list[BumpaResponse] = []
        datasets: list[ProviderDataset] = []
        orders: list[ProviderOrder] = []
        limit: int | None = None
        remaining: int | None = None
        orders_availability = "unavailable"
        orders_error: str | None = "Orders were not requested"
        exhausted_degradable_failures = 0
        successful_dataset_requests = 0
        first_degraded_error: BumpaProviderError | None = None

        for area, names in DATASETS.items():
            for name in names:
                try:
                    status, payload, headers = self.get_analytics(
                        area,
                        name,
                        date_from,
                        date_to,
                        # Bound worst-case extraction time after one endpoint has
                        # exhausted its retry budget, while still probing every
                        # independent dataset and orders endpoint.
                        max_attempts=1 if exhausted_degradable_failures else None,
                    )
                    decoded = decode_analytics_dataset(area, name, status, payload)
                    if decoded.availability == "available" and (
                        decoded.response_from is None
                        or decoded.response_to is None
                        or decoded.response_from.date() != date_from
                        or decoded.response_to.date() != date_to
                    ):
                        raise BumpaProviderError(
                            "Bumpa analytics response range did not match the request",
                            status_code=status,
                            failure_kind="invalid_response",
                        )
                except BumpaProviderError as exc:
                    # Analytics endpoints are independent. Preserve every typed
                    # endpoint failure and continue through orders; only global
                    # connection authentication/rate-limit failures abort.
                    if exc.failure_kind in {"authentication", "rate_limited"}:
                        raise
                    if _is_degradable_dataset_failure(exc):
                        exhausted_degradable_failures += 1
                        first_degraded_error = first_degraded_error or exc
                    failure_kind = _dataset_failure_kind(exc)
                    responses.append(
                        BumpaResponse(
                            area,
                            name,
                            exc.status_code,
                            {},
                            {},
                            "error",
                            str(exc),
                            failure_kind,
                            exc.retryable,
                            exc.request_id_hash,
                            exc.retry_after_seconds,
                        )
                    )
                    datasets.append(
                        ProviderDataset(
                            resource=area,
                            dataset=name,
                            availability="error",
                            payload={},
                            value=None,
                            title=f"{area}.{name}".replace("_", " ").title(),
                            error=str(exc),
                        )
                    )
                    continue
                availability, error = decoded.availability, decoded.error
                successful_dataset_requests += 1
                responses.append(
                    BumpaResponse(
                        area,
                        name,
                        status,
                        payload,
                        headers,
                        availability,
                        error,
                        "upstream_http" if availability == "error" else None,
                        status == 429 or status >= 500,
                        headers.get("x-request-id-sha256"),
                        _bounded_retry_after(headers.get("retry-after")),
                    )
                )
                datasets.append(decoded)
                limit, remaining = _merge_rate_limits(headers, limit, remaining)

        (
            orders,
            order_responses,
            orders_availability,
            orders_error,
            limit,
            remaining,
        ) = self._sync_orders(
            date_from,
            date_to,
            limit,
            remaining,
            max_attempts=1 if exhausted_degradable_failures else None,
        )
        responses.extend(order_responses)

        # Endpoint failures are independent and DATASETS ordering must never
        # decide whether useful data is retained. Escalate to the durable job
        # retry budget only after every analytics endpoint and orders have been
        # probed and none produced a schema-valid response.
        orders_retryably_failed = any(
            response.resource == "orders"
            and response.availability == "error"
            and response.retryable
            for response in order_responses
        )
        if (
            successful_dataset_requests == 0
            and first_degraded_error is not None
            and orders_retryably_failed
        ):
            raise first_degraded_error

        return BumpaSyncResult(
            datasets,
            orders,
            responses,
            limit,
            remaining,
            orders_availability,
            orders_error,
        )


def classify_availability(status_code: int, payload: dict[str, Any]) -> tuple[str, str | None]:
    message = payload.get("message") or payload.get("error")
    safe_message = (
        str(message) if isinstance(message, str) and message in KNOWN_UNAVAILABLE_MESSAGES else None
    )
    if not 200 <= status_code < 300:
        return "error", f"Bumpa request failed with HTTP {status_code}"
    if "error" in payload:
        return "unavailable", safe_message or "Bumpa reported unavailable data"
    if payload.get("success") is False:
        return "unavailable", safe_message or "Bumpa reported unavailable data"
    return "available", None


def decode_analytics_dataset(
    area: str, dataset: str, status_code: int, payload: dict[str, Any]
) -> ProviderDataset:
    """Validate and canonicalize one documented Bumpa analytics contract."""

    try:
        return _decode_analytics_dataset(area, dataset, status_code, payload)
    except BumpaProviderError:
        raise
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise _invalid_analytics_shape() from exc


def _decode_analytics_dataset(
    area: str, dataset: str, status_code: int, payload: dict[str, Any]
) -> ProviderDataset:

    availability, error = classify_availability(status_code, payload)
    title = f"{area}.{dataset}".replace("_", " ").title()
    if availability != "available":
        return ProviderDataset(
            resource=area,
            dataset=dataset,
            availability=availability,
            payload=payload,
            value=None,
            title=title,
            error=error,
        )

    key = (area, dataset)
    if key == ("sales", "overview"):
        canonical, value = _decode_overview(
            payload,
            primary_title="total sales",
            monetary_titles={"total sales", "offline sales", "total settled", "total owed"},
        )
        currency = _infer_currency(payload)
    elif key == ("customers", "overview"):
        canonical, value = _decode_overview(
            payload,
            primary_title="total customers",
            monetary_titles={"avg spend / customer"},
        )
        currency = None
    elif key in {
        ("sales", "total_sales"),
        ("sales", "gross_profit"),
        ("sales", "net_profit"),
    }:
        canonical, value = _decode_summary_chart(payload)
        currency = _infer_currency(payload)
    elif key == ("products", "products_sold"):
        canonical, value = _decode_summary_chart(payload)
        currency = None
    elif key == ("products", "overview"):
        canonical, value = _decode_products_overview(payload)
        currency = None
    elif key in {
        ("products", "top_selling_products"),
        ("products", "least_selling_products"),
    }:
        canonical = _decode_rankings(payload, customer_rows=False)
        value, currency = None, None
    elif key == ("customers", "top_customers_order"):
        canonical = _decode_rankings(payload, customer_rows=True)
        value, currency = None, None
    else:  # pragma: no cover - guarded by get_analytics
        raise ValueError("Unsupported Bumpa analytics dataset")

    response_from, response_to = _canonical_response_range(canonical)
    return ProviderDataset(
        resource=area,
        dataset=dataset,
        availability="available",
        payload=payload,
        value=value,
        title=title,
        error=None,
        canonical_payload=canonical,
        currency_code=currency,
        response_from=response_from,
        response_to=response_to,
    )


def _decode_overview(
    payload: dict[str, Any], *, primary_title: str, monetary_titles: set[str]
) -> tuple[dict[str, Any], Decimal | None]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, list):
        raise _invalid_analytics_shape()
    items: list[dict[str, Any]] = []
    primary: Decimal | None = None
    currency = _infer_currency(payload)
    for raw in raw_data:
        if not isinstance(raw, dict):
            raise _invalid_analytics_shape()
        title = raw.get("title")
        value = parse_money(raw.get("value"))
        if not isinstance(title, str) or not title.strip() or value is None:
            raise _invalid_analytics_shape()
        clean_title = title.strip()[:160]
        normalized_title = _normalise_label(clean_title)
        item: dict[str, Any] = {"title": clean_title, "value": _decimal_text(value)}
        if normalized_title in monetary_titles:
            item["unit"] = "currency"
            item["currency_code"] = currency
        else:
            item["unit"] = "count"
        items.append(item)
        if normalized_title == primary_title:
            primary = value
    if raw_data and primary is None:
        raise _invalid_analytics_shape()
    return {
        "schema_version": 1,
        "kind": "overview",
        "range": _decode_range(payload),
        "items": items,
    }, primary


def _decode_summary_chart(payload: dict[str, Any]) -> tuple[dict[str, Any], Decimal]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raise _invalid_analytics_shape()
    summary = raw_data.get("summary")
    chart = raw_data.get("chart")
    if not isinstance(summary, dict) or not isinstance(chart, dict):
        raise _invalid_analytics_shape()
    title = summary.get("title")
    value = parse_money(summary.get("value"))
    if not isinstance(title, str) or not title.strip() or value is None:
        raise _invalid_analytics_shape()
    canonical_summary: dict[str, Any] = {
        "title": title.strip()[:160],
        "value": _decimal_text(value),
    }
    if "progress" in summary:
        progress = parse_money(summary.get("progress"))
        if progress is None:
            raise _invalid_analytics_shape()
        canonical_summary["progress"] = _decimal_text(progress)
    if "progress_text" in summary:
        progress_text = summary.get("progress_text")
        if not isinstance(progress_text, str):
            raise _invalid_analytics_shape()
        canonical_summary["progress_text"] = progress_text[:160]
    return (
        {
            "schema_version": 1,
            "kind": "summary_chart",
            "range": _decode_range(payload),
            "summary": canonical_summary,
            "series": {
                "current_period": _decode_period(chart.get("current_period")),
                "previous_period": _decode_period(chart.get("previous_period")),
            },
        },
        value,
    )


def _decode_products_overview(payload: dict[str, Any]) -> tuple[dict[str, Any], Decimal]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raise _invalid_analytics_shape()
    summary: dict[str, dict[str, Any]] = {}
    total_products: Decimal | None = None
    currency = _infer_currency(payload)
    for name in ("total_products", "total_stock", "inventory_value"):
        raw_metric = raw_data.get(name)
        if not isinstance(raw_metric, dict):
            raise _invalid_analytics_shape()
        title = raw_metric.get("title")
        value = parse_money(raw_metric.get("value"))
        if not isinstance(title, str) or not title.strip() or value is None:
            raise _invalid_analytics_shape()
        summary[name] = {
            "title": title.strip()[:160],
            "value": _decimal_text(value),
            "unit": "currency" if name == "inventory_value" else "count",
        }
        if name == "inventory_value":
            summary[name]["currency_code"] = currency
        if name == "total_products":
            total_products = value
    assert total_products is not None
    return (
        {
            "schema_version": 1,
            "kind": "products_overview",
            "range": _decode_range(payload),
            "summary": summary,
            "series": {
                "total_products": _decode_period(raw_data.get("total_products_chart")),
                "total_products_sold": _decode_period(raw_data.get("total_products_sold_chart")),
            },
        },
        total_products,
    )


def _decode_rankings(payload: dict[str, Any], *, customer_rows: bool) -> dict[str, Any]:
    raw_data = payload.get("data")
    if customer_rows:
        if not isinstance(raw_data, dict) or not isinstance(raw_data.get("summary"), dict):
            raise _invalid_analytics_shape()
        raw_groups = [raw_data["summary"]]
    else:
        if not isinstance(raw_data, list):
            raise _invalid_analytics_shape()
        raw_groups = raw_data
    groups: list[dict[str, Any]] = []
    total_rows = 0
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            raise _invalid_analytics_shape()
        title = raw_group.get("title")
        rows = raw_group.get("data")
        if not isinstance(title, str) or not title.strip() or not isinstance(rows, list):
            raise _invalid_analytics_shape()
        canonical_rows: list[dict[str, str]] = []
        for rank, raw_row in enumerate(rows, start=1):
            if not isinstance(raw_row, dict):
                raise _invalid_analytics_shape()
            value = _first_decimal(
                raw_row,
                "value",
                "order_count",
                "orders",
                "count",
                "quantity",
                "sales",
            )
            label = _first_text(
                raw_row,
                "label",
                "name",
                "title",
                "product_name",
                "customer_name",
                "customer",
            )
            if value is None or label is None:
                raise _invalid_analytics_shape()
            row: dict[str, str] = {
                "rank": str(rank),
                "label": f"Customer {rank}" if customer_rows else label[:200],
                "value": _decimal_text(value),
            }
            if not customer_rows and (identifier := _first_text(raw_row, "id", "product_id")):
                row["id"] = identifier[:120]
            canonical_rows.append(row)
            total_rows += 1
            if total_rows > MAX_RANKING_ROWS:
                raise _invalid_analytics_shape()
        groups.append(
            {
                # Provider-controlled customer group titles are not business metrics
                # and could contain identity data. Keep the Hermes boundary fixed.
                "title": "Top customers" if customer_rows else title.strip()[:200],
                "rows": canonical_rows,
            }
        )
    return {
        "schema_version": 1,
        "kind": "ranking",
        "range": _decode_range(payload),
        "groups": groups,
    }


def _decode_period(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid_analytics_shape()
    raw_range = value.get("range")
    raw_points = value.get("data")
    if (
        not isinstance(raw_range, list)
        or not all(isinstance(item, str) for item in raw_range)
        or not isinstance(raw_points, list)
        or len(raw_points) > MAX_ANALYTICS_POINTS
    ):
        raise _invalid_analytics_shape()
    points: list[dict[str, Any]] = []
    for raw in raw_points:
        if not isinstance(raw, dict):
            raise _invalid_analytics_shape()
        index = raw.get("index")
        label = raw.get("dateLabel")
        metric = parse_money(raw.get("value"))
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not isinstance(label, str)
            or metric is None
        ):
            raise _invalid_analytics_shape()
        points.append({"index": index, "label": label[:80], "value": _decimal_text(metric)})
    return {"range": [item[:40] for item in raw_range], "points": points}


def _decode_range(payload: dict[str, Any]) -> dict[str, str]:
    raw_range = payload.get("range")
    if not isinstance(raw_range, dict):
        raise _invalid_analytics_shape()
    date_from, date_to = raw_range.get("from"), raw_range.get("to")
    if not isinstance(date_from, str) or not isinstance(date_to, str):
        raise _invalid_analytics_shape()
    parsed_from = _parse_range_datetime(date_from)
    parsed_to = _parse_range_datetime(date_to)
    if parsed_from is None or parsed_to is None or parsed_from > parsed_to:
        raise _invalid_analytics_shape()
    return {"from": parsed_from.date().isoformat(), "to": parsed_to.date().isoformat()}


def _canonical_response_range(canonical: dict[str, Any]) -> tuple[datetime, datetime]:
    value = canonical.get("range")
    if not isinstance(value, dict):  # pragma: no cover - all decoders require it
        raise _invalid_analytics_shape()
    parsed_from = _parse_range_datetime(value.get("from"))
    parsed_to = _parse_range_datetime(value.get("to"))
    if parsed_from is None or parsed_to is None:
        raise _invalid_analytics_shape()
    return parsed_from, parsed_to


def _parse_range_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _invalid_analytics_shape() -> BumpaProviderError:
    return BumpaProviderError(
        "Bumpa returned an unsupported analytics response",
        status_code=200,
        failure_kind="invalid_response",
    )


def _normalise_label(value: str) -> str:
    return " ".join(value.strip().lower().replace(".", "").split())


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _first_decimal(payload: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        if key in payload and (value := parse_money(payload[key])) is not None:
            return value
    return None


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return None


def _infer_currency(payload: dict[str, Any]) -> str | None:
    currencies: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"currency", "currency_code"} and isinstance(item, str):
                    code = item.strip().upper()
                    if len(code) == 3 and code.isalpha():
                        currencies.add(code)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and "₦" in value:
            currencies.add("NGN")

    visit(payload)
    return next(iter(currencies)) if len(currencies) == 1 else None


def _payload_identifies_scope(payload: dict[str, Any], scope_type: str, scope_id: str) -> bool:
    accepted_keys = {scope_type}
    if scope_type == "business_id":
        accepted_keys.add("store_id")

    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                (key in accepted_keys and str(item) == scope_id) or visit(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(visit(item) for item in value)
        return False

    return visit(payload)


def _dataset_failure_kind(exc: BumpaProviderError) -> DatasetFailureKind:
    if exc.failure_kind == "timeout":
        return "timeout"
    if exc.failure_kind == "transport":
        return "transport"
    if exc.failure_kind in {"invalid_response", "response_too_large"}:
        return "invalid_response"
    return "upstream_http"


def _is_degradable_dataset_failure(exc: BumpaProviderError) -> bool:
    return (
        exc.retryable
        and exc.failure_kind in {"timeout", "transport", "provider"}
        and (exc.status_code is None or exc.status_code >= 500)
    )


def _bounded_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), 10.0)
    except ValueError:
        return None


def _merge_rate_limits(
    headers: dict[str, str], current_limit: int | None, current_remaining: int | None
) -> tuple[int | None, int | None]:
    parsed_limit = _nonnegative_int(headers.get("x-ratelimit-limit"))
    parsed_remaining = _nonnegative_int(headers.get("x-ratelimit-remaining"))
    limit = parsed_limit if parsed_limit is not None else current_limit
    remaining_values = [
        value for value in (current_remaining, parsed_remaining) if value is not None
    ]
    remaining = min(remaining_values) if remaining_values else None
    if limit is not None and remaining is not None:
        remaining = min(remaining, limit)
    return limit, remaining


def _nonnegative_int(value: str | None) -> int | None:
    try:
        return max(int(value), 0) if value is not None else None
    except ValueError:
        return None


def _decode_orders_page(
    payload: dict[str, Any], requested_page: int
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Decode the documented ``{success, orders: LaravelPaginator}`` envelope."""

    envelope = payload.get("orders")
    if payload.get("success") is not True or not isinstance(envelope, dict):
        raise BumpaProviderError(
            "Bumpa returned an unsupported orders response",
            status_code=200,
            failure_kind="invalid_response",
        )
    raw_orders = envelope.get("data")
    current = envelope.get("current_page")
    last = envelope.get("last_page")
    total = envelope.get("total")
    per_page = envelope.get("per_page")
    if (
        not isinstance(raw_orders, list)
        or not isinstance(current, int)
        or isinstance(current, bool)
        or not isinstance(last, int)
        or isinstance(last, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or not isinstance(per_page, int)
        or isinstance(per_page, bool)
        or any(not isinstance(item, dict) for item in raw_orders)
    ):
        raise BumpaProviderError(
            "Bumpa returned an invalid orders page",
            status_code=200,
            failure_kind="invalid_response",
        )
    if (
        current < 1
        or last < current
        or total < 0
        or per_page < 1
        or current != requested_page
        or len(raw_orders) > per_page
    ):
        raise BumpaProviderError(
            "Bumpa returned an invalid orders page",
            status_code=200,
            failure_kind="invalid_response",
        )
    typed_orders = [item for item in raw_orders if isinstance(item, dict)]
    return typed_orders, current, last, total


def _normalise_order(payload: dict[str, Any]) -> ProviderOrder:
    identifier = payload.get("id") or payload.get("order_id") or payload.get("uuid")
    if identifier is None:
        raise BumpaProviderError("Bumpa order is missing an identifier")
    order_id = _bounded_provider_identifier(str(identifier), limit=120)
    raw_currency = payload.get("currency") or payload.get("currency_code")
    currency = str(raw_currency).strip().upper() if raw_currency is not None else None
    if currency is not None and (len(currency) != 3 or not currency.isalpha()):
        currency = None
    order_date = _parse_datetime(
        payload.get("order_date") or payload.get("created_at") or payload.get("date")
    )
    return ProviderOrder(
        order_id=order_id,
        order_number=str(payload.get("order_number") or payload.get("number") or identifier)[:120],
        status=str(payload.get("status") or "unknown")[:80],
        payment_status=str(payload.get("payment_status") or "unknown")[:80],
        currency_code=currency,
        # Missing or malformed money stays unavailable. Treating it as zero would
        # silently turn an incomplete provider record into a real commercial fact.
        total_amount=parse_money(
            payload["total_amount"]
            if "total_amount" in payload
            else payload.get("total", payload.get("grand_total"))
        ),
        order_date=order_date,
        payload=payload,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _bounded_provider_identifier(value: str, *, limit: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise BumpaProviderError(
            "Bumpa order is missing an identifier", failure_kind="invalid_response"
        )
    if len(normalized) <= limit:
        return normalized
    return f"sha256:{hashlib.sha256(normalized.encode()).hexdigest()}"
