from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from app.core.store_context import validate_store_currency, validate_store_timezone
from app.providers.contracts import ProviderDataset, ProviderOrder
from app.providers.diagnostics import provider_request_id_hash
from app.providers.redaction import parse_money

BUMPA_BASE_URL = "https://api.getbumpa.com/api"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ORDER_PAGES = 1_000
MAX_ORDER_RECORDS = 100_000
MAX_ANALYTICS_POINTS = 2_000
MAX_RANKING_ROWS = 500


@dataclass(frozen=True, slots=True)
class BumpaEndpointRequestPolicy:
    """Timeout and retry budget for an explicitly identified Bumpa endpoint."""

    connect_seconds: float
    read_seconds: float
    write_seconds: float
    pool_seconds: float
    max_attempts: int

    def to_httpx(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_seconds,
            read=self.read_seconds,
            write=self.write_seconds,
            pool=self.pool_seconds,
        )


DEFAULT_REQUEST_POLICY = BumpaEndpointRequestPolicy(
    connect_seconds=5.0,
    read_seconds=30.0,
    write_seconds=30.0,
    pool_seconds=30.0,
    max_attempts=3,
)
# Bumpa's product overview aggregation can legitimately take close to a minute
# while the remaining analytics and orders endpoints complete within the normal
# budget. Keep its larger read-inactivity window and two-attempt ceiling isolated
# to this exact endpoint; response bodies remain independently size-bounded.
ANALYTICS_REQUEST_POLICIES: dict[tuple[str, str], BumpaEndpointRequestPolicy] = {
    ("products", "overview"): BumpaEndpointRequestPolicy(
        connect_seconds=5.0,
        read_seconds=90.0,
        write_seconds=30.0,
        pool_seconds=30.0,
        max_attempts=2,
    )
}
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
        store_timezone: str,
        store_currency: str,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = DEFAULT_REQUEST_POLICY.max_attempts,
    ) -> None:
        if scope_type not in {"business_id", "location_id"}:
            raise ValueError("Invalid Bumpa scope_type")
        if not api_key.strip() or not scope_id.strip():
            raise ValueError("Bumpa credentials and scope are required")
        timezone_name = validate_store_timezone(store_timezone)
        self._api_key = api_key
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.store_timezone = timezone_name
        self._store_zone = ZoneInfo(timezone_name)
        self.store_currency = validate_store_currency(store_currency)
        self._client = client or httpx.Client(
            base_url=BUMPA_BASE_URL,
            timeout=DEFAULT_REQUEST_POLICY.to_httpx(),
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
        request_policy: BumpaEndpointRequestPolicy | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        requested_attempt_limit = self._max_attempts if max_attempts is None else max_attempts
        if not 1 <= requested_attempt_limit <= self._max_attempts:
            raise ValueError("Invalid Bumpa request attempt limit")
        attempt_limit = (
            min(requested_attempt_limit, request_policy.max_attempts)
            if request_policy is not None
            else requested_attempt_limit
        )
        for attempt in range(1, attempt_limit + 1):
            try:
                if request_policy is None:
                    response = self._client.get(path, params=params, headers=self._headers)
                else:
                    response = self._client.get(
                        path,
                        params=params,
                        headers=self._headers,
                        timeout=request_policy.to_httpx(),
                    )
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
        return self._request(
            f"/commerce/v1/analytics/{area}",
            params,
            max_attempts=max_attempts,
            request_policy=ANALYTICS_REQUEST_POLICIES.get((area, dataset)),
        )

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
        offending_status: int | None = None
        offending_payload: dict[str, Any] = {}
        offending_headers: dict[str, str] = {}
        try:
            while page <= MAX_ORDER_PAGES:
                # Reset the evidence boundary before I/O so a transport failure on
                # a later page can never be attributed to the previous response.
                offending_status = None
                offending_payload = {}
                offending_headers = {}
                status, payload, headers = self.get_orders_page(
                    date_from,
                    date_to,
                    page,
                    max_attempts=max_attempts,
                )
                offending_status = status
                offending_payload = payload
                offending_headers = headers
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
                    order = _normalise_order(row, store_currency=self.store_currency)
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
                if current < last and not raw_orders:
                    raise BumpaProviderError(
                        "Bumpa orders pagination did not advance",
                        status_code=status,
                        failure_kind="invalid_response",
                    )
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
                    offending_status = None
                    offending_payload = {}
                    offending_headers = {}
                    break
                page = current + 1
                offending_status = None
                offending_payload = {}
                offending_headers = {}
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
                    exc.status_code if exc.status_code is not None else offending_status,
                    offending_payload,
                    offending_headers,
                    availability,
                    error,
                    evidence_kind,
                    exc.retryable,
                    exc.request_id_hash or offending_headers.get("x-request-id-sha256"),
                    exc.retry_after_seconds
                    if exc.retry_after_seconds is not None
                    else _bounded_retry_after(offending_headers.get("retry-after")),
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
        today = datetime.now(self._store_zone).date()
        date_from = date(today.year - 1, today.month, min(today.day, 28))
        verified: list[ProviderDataset] = []
        scope_identified = False
        for area, dataset in (("sales", "overview"), ("customers", "overview")):
            status, payload, headers = self.get_analytics(area, dataset, date_from, today)
            decoded = decode_analytics_dataset(
                area,
                dataset,
                status,
                payload,
                store_timezone=self.store_timezone,
                store_currency=self.store_currency,
            )
            if decoded.availability != "available":
                raise BumpaProviderError(
                    decoded.error or "Bumpa scope could not be verified",
                    status_code=status,
                    retryable=status == 429 or status >= 500,
                    failure_kind=("provider" if status >= 500 else "invalid_response"),
                    request_id_hash=headers.get("x-request-id-sha256"),
                    retry_after_seconds=_bounded_retry_after(headers.get("retry-after")),
                )
            if not _response_range_matches_request(
                payload,
                decoded.response_from,
                decoded.response_to,
                date_from,
                today,
                store_timezone=self._store_zone,
            ):
                raise BumpaProviderError(
                    "Bumpa analytics response range did not match the request",
                    status_code=status,
                    failure_kind="invalid_response",
                    request_id_hash=headers.get("x-request-id-sha256"),
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
                status: int | None = None
                payload: dict[str, Any] = {}
                headers: dict[str, str] = {}
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
                    decoded = decode_analytics_dataset(
                        area,
                        name,
                        status,
                        payload,
                        store_timezone=self.store_timezone,
                        store_currency=self.store_currency,
                    )
                    if decoded.availability == "available" and not _response_range_matches_request(
                        payload,
                        decoded.response_from,
                        decoded.response_to,
                        date_from,
                        date_to,
                        store_timezone=self._store_zone,
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
                            exc.status_code if exc.status_code is not None else status,
                            payload,
                            headers,
                            "error",
                            str(exc),
                            failure_kind,
                            exc.retryable,
                            exc.request_id_hash or headers.get("x-request-id-sha256"),
                            exc.retry_after_seconds
                            if exc.retry_after_seconds is not None
                            else _bounded_retry_after(headers.get("retry-after")),
                        )
                    )
                    datasets.append(
                        ProviderDataset(
                            resource=area,
                            dataset=name,
                            availability="error",
                            payload=payload,
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
    area: str,
    dataset: str,
    status_code: int,
    payload: dict[str, Any],
    *,
    store_timezone: str,
    store_currency: str,
) -> ProviderDataset:
    """Validate and canonicalize one documented Bumpa analytics contract."""

    try:
        return _decode_analytics_dataset(
            area,
            dataset,
            status_code,
            payload,
            store_timezone=ZoneInfo(validate_store_timezone(store_timezone)),
            store_currency=validate_store_currency(store_currency),
        )
    except BumpaProviderError:
        raise
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise _invalid_analytics_shape() from exc


def _decode_analytics_dataset(
    area: str,
    dataset: str,
    status_code: int,
    payload: dict[str, Any],
    *,
    store_timezone: ZoneInfo,
    store_currency: str,
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
            store_timezone=store_timezone,
            store_currency=store_currency,
        )
        currency = _resolve_currency(payload, store_currency)
    elif key == ("customers", "overview"):
        canonical, value = _decode_overview(
            payload,
            primary_title="total customers",
            monetary_titles={"avg spend / customer"},
            store_timezone=store_timezone,
            store_currency=store_currency,
        )
        currency = None
    elif key in {
        ("sales", "total_sales"),
        ("sales", "gross_profit"),
        ("sales", "net_profit"),
    }:
        canonical, value = _decode_summary_chart(
            payload,
            store_timezone=store_timezone,
            store_currency=store_currency,
            count_metric=False,
        )
        currency = _resolve_currency(payload, store_currency)
    elif key == ("products", "products_sold"):
        canonical, value = _decode_summary_chart(
            payload,
            store_timezone=store_timezone,
            store_currency=store_currency,
            count_metric=True,
        )
        currency = None
    elif key == ("products", "overview"):
        canonical, value = _decode_products_overview(
            payload,
            store_timezone=store_timezone,
            store_currency=store_currency,
        )
        currency = None
    elif key in {
        ("products", "top_selling_products"),
        ("products", "least_selling_products"),
    }:
        canonical = _decode_rankings(
            payload,
            customer_rows=False,
            store_timezone=store_timezone,
            store_currency=store_currency,
        )
        value, currency = None, None
    elif key == ("customers", "top_customers_order"):
        canonical = _decode_rankings(
            payload,
            customer_rows=True,
            store_timezone=store_timezone,
            store_currency=store_currency,
        )
        value, currency = None, None
    else:  # pragma: no cover - guarded by get_analytics
        raise ValueError("Unsupported Bumpa analytics dataset")

    response_from, response_to = _response_range(payload)
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
    payload: dict[str, Any],
    *,
    primary_title: str,
    monetary_titles: set[str],
    store_timezone: ZoneInfo,
    store_currency: str,
) -> tuple[dict[str, Any], Decimal | None]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, list):
        raise _invalid_analytics_shape()
    items: list[dict[str, Any]] = []
    primary: Decimal | None = None
    currency = _resolve_currency(payload, store_currency)
    for raw in raw_data:
        if not isinstance(raw, dict):
            raise _invalid_analytics_shape()
        title = raw.get("title")
        value = parse_money(raw.get("value"), currency_code=store_currency)
        if not isinstance(title, str) or not title.strip() or value is None:
            raise _invalid_analytics_shape()
        clean_title = title.strip()[:160]
        normalized_title = _normalise_label(clean_title)
        if normalized_title not in monetary_titles and not _is_nonnegative_integral(value):
            raise _invalid_analytics_shape()
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
        "range": _decode_range(payload, store_timezone=store_timezone),
        "items": items,
    }, primary


def _decode_summary_chart(
    payload: dict[str, Any],
    *,
    store_timezone: ZoneInfo,
    store_currency: str,
    count_metric: bool,
) -> tuple[dict[str, Any], Decimal]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raise _invalid_analytics_shape()
    summary = raw_data.get("summary")
    chart = raw_data.get("chart")
    if not isinstance(summary, dict) or not isinstance(chart, dict):
        raise _invalid_analytics_shape()
    title = summary.get("title")
    value = parse_money(summary.get("value"), currency_code=store_currency)
    if (
        not isinstance(title, str)
        or not title.strip()
        or value is None
        or (count_metric and not _is_nonnegative_integral(value))
    ):
        raise _invalid_analytics_shape()
    canonical_summary: dict[str, Any] = {
        "title": title.strip()[:160],
        "value": _decimal_text(value),
    }
    if "progress" in summary:
        progress = parse_money(summary.get("progress"), currency_code=store_currency)
        if progress is None:
            raise _invalid_analytics_shape()
        canonical_summary["progress"] = _decimal_text(progress)
    if "progress_text" in summary:
        progress_text = summary.get("progress_text")
        if not isinstance(progress_text, str):
            raise _invalid_analytics_shape()
        canonical_summary["progress_text"] = progress_text[:160]
    response_range = _response_range(payload)
    current_dates = _range_local_dates(payload.get("range"), store_timezone=store_timezone)
    previous_period = chart.get("previous_period")
    previous_dates = _period_local_dates(
        previous_period,
        store_timezone=store_timezone,
    )
    if not _valid_previous_local_range(current_dates, previous_dates):
        raise _invalid_analytics_shape()
    return (
        {
            "schema_version": 1,
            "kind": "summary_chart",
            "range": _decode_range(payload, store_timezone=store_timezone),
            "summary": canonical_summary,
            "series": {
                "current_period": _decode_period(
                    chart.get("current_period"),
                    store_timezone=store_timezone,
                    store_currency=store_currency,
                    count_values=count_metric,
                    expected_ranges=(response_range,),
                    expected_local_date_ranges=(current_dates,),
                ),
                "previous_period": _decode_period(
                    previous_period,
                    store_timezone=store_timezone,
                    store_currency=store_currency,
                    count_values=count_metric,
                    expected_ranges=(
                        _inclusive_local_day_bounds(previous_dates, store_timezone=store_timezone),
                    ),
                    expected_local_date_ranges=(previous_dates,),
                ),
            },
        },
        value,
    )


def _decode_products_overview(
    payload: dict[str, Any], *, store_timezone: ZoneInfo, store_currency: str
) -> tuple[dict[str, Any], Decimal]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raise _invalid_analytics_shape()
    summary: dict[str, dict[str, Any]] = {}
    total_products: Decimal | None = None
    currency = _resolve_currency(payload, store_currency)
    for name in ("total_products", "total_stock", "inventory_value"):
        raw_metric = raw_data.get(name)
        if not isinstance(raw_metric, dict):
            raise _invalid_analytics_shape()
        title = raw_metric.get("title")
        value = parse_money(raw_metric.get("value"), currency_code=store_currency)
        if (
            not isinstance(title, str)
            or not title.strip()
            or value is None
            or (name != "inventory_value" and not _is_nonnegative_integral(value))
        ):
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
    response_range = _response_range(payload)
    current_dates = _range_local_dates(payload.get("range"), store_timezone=store_timezone)
    return (
        {
            "schema_version": 1,
            "kind": "products_overview",
            "range": _decode_range(payload, store_timezone=store_timezone),
            "summary": summary,
            "series": {
                "total_products": _decode_period(
                    raw_data.get("total_products_chart"),
                    store_timezone=store_timezone,
                    store_currency=store_currency,
                    count_values=True,
                    expected_ranges=(response_range,),
                    expected_local_date_ranges=(current_dates,),
                ),
                "total_products_sold": _decode_period(
                    raw_data.get("total_products_sold_chart"),
                    store_timezone=store_timezone,
                    store_currency=store_currency,
                    count_values=True,
                    expected_ranges=(response_range,),
                    expected_local_date_ranges=(current_dates,),
                ),
            },
        },
        total_products,
    )


def _decode_rankings(
    payload: dict[str, Any],
    *,
    customer_rows: bool,
    store_timezone: ZoneInfo,
    store_currency: str,
) -> dict[str, Any]:
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
            value_keys = (
                ("value", "order_count", "orders", "count")
                if customer_rows
                else ("value", "count", "quantity", "sales")
            )
            value = _first_decimal(
                raw_row,
                *value_keys,
                currency_code=store_currency,
            )
            # Customer labels are PII and are deliberately replaced with stable
            # positional labels. Bumpa also returns anonymous/deleted customers
            # with an empty label and ID; their aggregate order count is still a
            # valid fact. Product rankings, however, require a display label.
            label = None
            if not customer_rows:
                label = _first_text(
                    raw_row,
                    "label",
                    "name",
                    "title",
                    "product_name",
                )
            if (
                value is None
                or not _is_nonnegative_integral(value)
                or (not customer_rows and label is None)
            ):
                raise _invalid_analytics_shape()
            display_label = f"Customer {rank}" if customer_rows else label
            assert display_label is not None
            row: dict[str, str] = {
                "rank": str(rank),
                "label": display_label[:200],
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
        "range": _decode_range(payload, store_timezone=store_timezone),
        "groups": groups,
    }


def _decode_period(
    value: Any,
    *,
    store_timezone: ZoneInfo,
    store_currency: str,
    count_values: bool,
    expected_ranges: tuple[tuple[datetime, datetime], ...] = (),
    expected_local_date_ranges: tuple[tuple[date, date], ...] = (),
) -> dict[str, Any]:
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
    parsed_from = _parse_range_datetime(raw_range[0]) if len(raw_range) == 2 else None
    parsed_to = _parse_range_datetime(raw_range[1]) if len(raw_range) == 2 else None
    local_dates = (
        _range_local_dates(raw_range, store_timezone=store_timezone)
        if len(raw_range) == 2
        else None
    )
    date_only_range = _range_uses_date_only(raw_range) if len(raw_range) == 2 else False
    if (
        parsed_from is None
        or parsed_to is None
        or parsed_from > parsed_to
        or (
            expected_ranges
            and not date_only_range
            and (parsed_from, parsed_to) not in expected_ranges
        )
        or (expected_local_date_ranges and local_dates not in expected_local_date_ranges)
    ):
        raise _invalid_analytics_shape()
    points: list[dict[str, Any]] = []
    for raw in raw_points:
        if not isinstance(raw, dict):
            raise _invalid_analytics_shape()
        index = raw.get("index")
        label = raw.get("dateLabel")
        metric = parse_money(raw.get("value"), currency_code=store_currency)
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not isinstance(label, str)
            or metric is None
            or (count_values and not _is_nonnegative_integral(metric))
        ):
            raise _invalid_analytics_shape()
        points.append({"index": index, "label": label[:80], "value": _decimal_text(metric)})
    return {
        "range": [
            _canonical_range_value(raw_range[0], parsed_from, store_timezone),
            _canonical_range_value(raw_range[1], parsed_to, store_timezone),
        ],
        "points": points,
    }


def _decode_range(payload: dict[str, Any], *, store_timezone: ZoneInfo) -> dict[str, str]:
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
    return {
        "from": _canonical_range_value(date_from, parsed_from, store_timezone),
        "to": _canonical_range_value(date_to, parsed_to, store_timezone),
    }


def _response_range(payload: dict[str, Any]) -> tuple[datetime, datetime]:
    value = payload.get("range")
    if not isinstance(value, dict):
        raise _invalid_analytics_shape()
    parsed_from = _parse_range_datetime(value.get("from"))
    parsed_to = _parse_range_datetime(value.get("to"))
    if parsed_from is None or parsed_to is None or parsed_from > parsed_to:
        raise _invalid_analytics_shape()
    return parsed_from, parsed_to


def _canonical_range_value(raw: str, parsed: datetime, store_timezone: ZoneInfo) -> str:
    # Canonical schema v1 always stores provider-local calendar dates. Preserve
    # the exact UTC evidence separately in ``response_from``/``response_to``.
    if _is_date_only(raw):
        return parsed.date().isoformat()
    return parsed.astimezone(store_timezone).date().isoformat()


def _response_range_matches_request(
    payload: dict[str, Any],
    response_from: datetime | None,
    response_to: datetime | None,
    requested_from: date,
    requested_to: date,
    *,
    store_timezone: ZoneInfo,
) -> bool:
    """Validate Bumpa's inclusive local-day window without losing timezone evidence.

    Bumpa accepts date-only query parameters but its live analytics responses
    normalize the store-local day boundaries to UTC. A UTC+01:00 store therefore
    returns 23:00 on the previous UTC date through 22:59:59.999999 on the requested
    final date. Comparing UTC calendar dates rejects that valid response. Instead,
    require the exact configured store-local day window. This is an explicit
    connection contract, not the tenant's editable display/scheduling timezone.
    """

    raw_range = payload.get("range")
    if (
        response_from is None
        or response_to is None
        or not isinstance(raw_range, dict)
        or requested_from > requested_to
    ):
        return False
    raw_from, raw_to = raw_range.get("from"), raw_range.get("to")
    if not isinstance(raw_from, str) or not isinstance(raw_to, str):
        return False
    expected_from = datetime.combine(
        requested_from, datetime.min.time(), store_timezone
    ).astimezone(UTC)
    expected_to = datetime.combine(requested_to, datetime.max.time(), store_timezone).astimezone(
        UTC
    )
    return response_from == expected_from and response_to == expected_to


def _range_local_dates(value: Any, *, store_timezone: ZoneInfo) -> tuple[date, date]:
    if isinstance(value, dict):
        raw_from, raw_to = value.get("from"), value.get("to")
    elif isinstance(value, list) and len(value) == 2:
        raw_from, raw_to = value
    else:
        raise _invalid_analytics_shape()
    if not isinstance(raw_from, str) or not isinstance(raw_to, str):
        raise _invalid_analytics_shape()
    parsed_from = _parse_range_datetime(raw_from)
    parsed_to = _parse_range_datetime(raw_to)
    if parsed_from is None or parsed_to is None or parsed_from > parsed_to:
        raise _invalid_analytics_shape()
    local_from = (
        parsed_from.date()
        if _is_date_only(raw_from)
        else parsed_from.astimezone(store_timezone).date()
    )
    local_to = (
        parsed_to.date() if _is_date_only(raw_to) else parsed_to.astimezone(store_timezone).date()
    )
    if local_from > local_to:
        raise _invalid_analytics_shape()
    return local_from, local_to


def _period_local_dates(value: Any, *, store_timezone: ZoneInfo) -> tuple[date, date]:
    if not isinstance(value, dict):
        raise _invalid_analytics_shape()
    return _range_local_dates(value.get("range"), store_timezone=store_timezone)


def _valid_previous_local_range(current: tuple[date, date], previous: tuple[date, date]) -> bool:
    """Accept Bumpa's bounded adjacent comparison range.

    Calendar-month boundaries can make the provider's previous period up to
    three days longer or shorter than the requested current period. Require
    immediate adjacency and that tight calendar bound; gaps, overlaps, clipped
    timestamps, and unbounded historical ranges remain invalid.
    """

    current_days = (current[1] - current[0]).days + 1
    previous_days = (previous[1] - previous[0]).days + 1
    return previous[1] == current[0] - timedelta(days=1) and abs(previous_days - current_days) <= 3


def _inclusive_local_day_bounds(
    value: tuple[date, date], *, store_timezone: ZoneInfo
) -> tuple[datetime, datetime]:
    return (
        datetime.combine(value[0], datetime.min.time(), store_timezone).astimezone(UTC),
        datetime.combine(value[1], datetime.max.time(), store_timezone).astimezone(UTC),
    )


def _range_uses_date_only(value: Any) -> bool:
    if isinstance(value, dict):
        values = (value.get("from"), value.get("to"))
    elif isinstance(value, list) and len(value) == 2:
        values = (value[0], value[1])
    else:
        raise _invalid_analytics_shape()
    return all(_is_date_only(item) for item in values)


def _is_date_only(value: Any) -> bool:
    if not isinstance(value, str) or len(value.strip()) != 10:
        return False
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def _parse_range_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if _is_date_only(raw):
        return datetime.combine(date.fromisoformat(raw), datetime.min.time(), UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A time-bearing provider range without an offset is ambiguous. Never
    # silently interpret it as UTC; Bumpa must provide ``Z`` or an explicit
    # numeric offset. Date-only nested comparison ranges are handled above.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


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


def _is_nonnegative_integral(value: Decimal) -> bool:
    return value >= 0 and value == value.to_integral_value()


def _first_decimal(payload: dict[str, Any], *keys: str, currency_code: str) -> Decimal | None:
    for key in keys:
        if (
            key in payload
            and (value := parse_money(payload[key], currency_code=currency_code)) is not None
        ):
            return value
    return None


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return None


def _resolve_currency(payload: dict[str, Any], store_currency: str) -> str:
    currencies: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"currency", "currency_code"}:
                    if not isinstance(item, str):
                        raise _invalid_analytics_shape()
                    code = item.strip().upper()
                    if len(code) != 3 or not code.isascii() or not code.isalpha():
                        raise _invalid_analytics_shape()
                    currencies.add(code)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            for symbol, code in {
                "₦": "NGN",
                "£": "GBP",
                "€": "EUR",
                "₹": "INR",
                "₵": "GHS",
            }.items():
                if symbol in value:
                    currencies.add(code)
            if re.search(r"(?i)(?:^|\s)KSh\s*[-+]?\d", value):
                currencies.add("KES")

    visit(payload)
    if len(currencies) > 1 or (currencies and currencies != {store_currency}):
        raise _invalid_analytics_shape()
    return store_currency


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


def _normalise_order(payload: dict[str, Any], *, store_currency: str) -> ProviderOrder:
    identifier = payload.get("id") or payload.get("order_id") or payload.get("uuid")
    if identifier is None:
        raise BumpaProviderError("Bumpa order is missing an identifier")
    order_id = _bounded_provider_identifier(str(identifier), limit=120)
    # Resolve every explicit order and line-item currency before parsing any
    # commercial value. That prevents a mixed-currency order from being partly
    # interpreted using whichever claim happened to appear first.
    currency = _resolve_order_currency(payload, store_currency=store_currency)
    order_date = _parse_datetime(
        payload.get("order_date") or payload.get("created_at") or payload.get("date")
    )
    total_amount = _preferred_order_total(
        payload,
        currency_code=currency,
    )
    _validate_order_commercial_facts(payload, currency_code=currency)
    return ProviderOrder(
        order_id=order_id,
        order_number=str(payload.get("order_number") or payload.get("number") or identifier)[:120],
        status=str(payload.get("status") or "unknown")[:80],
        payment_status=str(payload.get("payment_status") or "unknown")[:80],
        currency_code=currency,
        # A genuinely absent amount stays unavailable. A present malformed amount
        # fails the page closed instead of silently becoming a missing commercial fact.
        total_amount=total_amount,
        order_date=order_date,
        payload=payload,
    )


def _resolve_order_currency(payload: dict[str, Any], *, store_currency: str) -> str:
    currency_claims: set[str] = set()

    def collect_claims(value: dict[str, Any]) -> None:
        for key in ("currency", "currency_code"):
            if key not in value or value[key] is None:
                continue
            raw_currency = value[key]
            if not isinstance(raw_currency, str):
                currency_claims.add("")
                continue
            currency_claims.add(raw_currency.strip().upper())

    def visit_item(value: dict[str, Any]) -> None:
        collect_claims(value)
        for nested in value.values():
            if isinstance(nested, dict):
                visit_item(nested)
            elif isinstance(nested, list):
                for row in nested:
                    if isinstance(row, dict):
                        visit_item(row)

    collect_claims(payload)
    for container_key in ("items", "order_items", "products"):
        if container_key not in payload or payload[container_key] is None:
            continue
        raw_items = payload[container_key]
        if not isinstance(raw_items, list) or any(not isinstance(item, dict) for item in raw_items):
            raise BumpaProviderError(
                "Bumpa order contained invalid line items",
                failure_kind="invalid_response",
            )
        for item in raw_items:
            assert isinstance(item, dict)
            visit_item(item)

    fallback = store_currency.strip().upper()
    currency = next(iter(currency_claims)) if len(currency_claims) == 1 else fallback
    if (
        len(currency_claims) > 1
        or len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise BumpaProviderError(
            "Bumpa order contained an invalid or conflicting currency",
            failure_kind="invalid_response",
        )
    return currency


def _validated_money_aliases(
    payload: dict[str, Any],
    *keys: str,
    currency_code: str,
    nonnegative: bool = False,
) -> Decimal | None:
    parsed_values: list[Decimal] = []
    for key in keys:
        if key not in payload:
            continue
        value = parse_money(payload[key], currency_code=currency_code)
        if value is None or (nonnegative and value < 0):
            raise BumpaProviderError(
                "Bumpa order contained an invalid monetary value",
                failure_kind="invalid_response",
            )
        parsed_values.append(value)
    if len(set(parsed_values)) > 1:
        raise BumpaProviderError(
            "Bumpa order contained conflicting monetary values",
            failure_kind="invalid_response",
        )
    return parsed_values[0] if parsed_values else None


def _preferred_order_total(payload: dict[str, Any], *, currency_code: str) -> Decimal | None:
    # The live orders contract exposes ``total`` as the final order amount and
    # may also include a semantically different legacy ``grand_total``. Treat
    # total_amount/total as canonical aliases, with grand_total only as a
    # backwards-compatible fallback. A present fallback is still syntax-checked
    # but is not required to equal the canonical total.
    canonical = _validated_money_aliases(
        payload,
        "total_amount",
        "total",
        currency_code=currency_code,
    )
    legacy = _validated_money_aliases(
        payload,
        "grand_total",
        currency_code=currency_code,
    )
    return canonical if canonical is not None else legacy


def _validate_order_commercial_facts(payload: dict[str, Any], *, currency_code: str) -> None:
    for aliases in (
        ("subtotal_amount", "subtotal", "sub_total"),
        ("tax_amount", "tax"),
        ("shipping_amount", "shipping_fee", "shipping_price"),
        ("amount_paid", "paid_amount"),
        ("amount_due", "due_amount"),
        ("discount_amount", "discount"),
        ("total_discount",),
    ):
        _validated_money_aliases(payload, *aliases, currency_code=currency_code)

    raw_items = payload.get("items") or payload.get("order_items") or payload.get("products") or []
    if not isinstance(raw_items, list) or any(not isinstance(item, dict) for item in raw_items):
        raise BumpaProviderError(
            "Bumpa order contained invalid line items",
            failure_kind="invalid_response",
        )
    for item in raw_items:
        assert isinstance(item, dict)
        _validated_money_aliases(
            item,
            "quantity",
            "qty",
            currency_code="UNSPECIFIED",
            nonnegative=True,
        )
        _validated_money_aliases(
            item,
            "unit_price",
            "price",
            currency_code=currency_code,
        )
        _validated_money_aliases(
            item,
            "total_amount",
            "total",
            currency_code=currency_code,
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
