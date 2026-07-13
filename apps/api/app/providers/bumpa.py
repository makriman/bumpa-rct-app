from __future__ import annotations

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
MAX_ORDER_PAGES = 100
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


BumpaFailureKind = Literal[
    "timeout",
    "transport",
    "rate_limited",
    "authentication",
    "provider",
    "invalid_response",
    "response_too_large",
]
DatasetFailureKind = Literal["timeout", "transport", "upstream_http"]


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
            except httpx.NetworkError as exc:
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

            # Authentication failures invalidate the connection rather than a
            # single dataset. Likewise, an exhausted upstream retry budget must
            # be retried by the durable job runtime; returning either response to
            # ``sync`` would incorrectly persist a partial-success run.
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
        self, date_from: date, date_to: date, page: int, *, limit: int = 100
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
        return self._request("/commerce/v1/orders", params)

    def verify(self) -> None:
        today = datetime.now(UTC).date()
        status, payload, headers = self.get_analytics("sales", "overview", today, today)
        availability, message = classify_availability(status, payload)
        if availability != "available":
            raise BumpaProviderError(
                message or "Bumpa connection verification failed",
                status_code=status,
                retryable=status == 429 or status >= 500,
                request_id_hash=headers.get("x-request-id-sha256"),
                retry_after_seconds=_bounded_retry_after(headers.get("retry-after")),
            )

    def sync(self, date_from: date, date_to: date) -> BumpaSyncResult:
        responses: list[BumpaResponse] = []
        datasets: list[ProviderDataset] = []
        orders: list[ProviderOrder] = []
        limit: int | None = None
        remaining: int | None = None
        orders_availability = "unavailable"
        orders_error: str | None = "Orders were not requested"
        successful_dataset_requests = 0
        degraded_dataset_failures = 0
        isolated_dataset_error: BumpaProviderError | None = None

        for area, names in DATASETS.items():
            for name in names:
                try:
                    status, payload, headers = self.get_analytics(
                        area,
                        name,
                        date_from,
                        date_to,
                        # Once one endpoint has exhausted its own retry budget,
                        # a single independent probe is enough to distinguish an
                        # isolated defect from a wider outage. Do not spend a
                        # second full retry budget before handing the job back to
                        # the durable queue.
                        max_attempts=1 if degraded_dataset_failures else None,
                    )
                except BumpaProviderError as exc:
                    # Retain at most one exhausted transport/upstream failure
                    # while probing the remaining independent datasets. A second
                    # failure is a wider outage and returns control to the
                    # durable job retry policy. The final all-failed guard keeps
                    # this decision independent of dataset ordering.
                    if not _is_degradable_dataset_failure(exc) or degraded_dataset_failures >= 1:
                        raise
                    degraded_dataset_failures += 1
                    isolated_dataset_error = exc
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
                            True,
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
                availability, error = classify_availability(status, payload)
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
                datasets.append(
                    ProviderDataset(
                        resource=area,
                        dataset=name,
                        availability=availability,
                        payload=payload,
                        value=_metric_value(payload) if availability == "available" else None,
                        title=f"{area}.{name}".replace("_", " ").title(),
                        error=error,
                    )
                )
                limit, remaining = _merge_rate_limits(headers, limit, remaining)

        if degraded_dataset_failures and successful_dataset_requests == 0:
            assert isolated_dataset_error is not None
            raise isolated_dataset_error

        page = 1
        while page <= MAX_ORDER_PAGES:
            status, payload, headers = self.get_orders_page(date_from, date_to, page)
            availability, error = classify_availability(status, payload)
            orders_availability = availability
            orders_error = error
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
            limit, remaining = _merge_rate_limits(headers, limit, remaining)
            if availability != "available":
                break
            raw_orders = payload.get("data", [])
            if not isinstance(raw_orders, list):
                raise BumpaProviderError("Bumpa orders response has an invalid data field")
            orders.extend(_normalise_order(row) for row in raw_orders if isinstance(row, dict))
            current, last = _pagination(payload, page)
            if current >= last:
                break
            if current < page:
                raise BumpaProviderError("Bumpa orders pagination did not advance")
            page = current + 1
        else:
            raise BumpaProviderError("Bumpa orders exceeded the pagination limit")

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
    safe_message = str(message)[:300] if message else None
    if status_code >= 400:
        return "error", safe_message or f"Bumpa request failed with HTTP {status_code}"
    if "error" in payload:
        return "unavailable", safe_message or "Bumpa reported unavailable data"
    return "available", None


def _is_degradable_dataset_failure(exc: BumpaProviderError) -> bool:
    return (
        exc.retryable
        and exc.failure_kind in {"timeout", "transport", "provider"}
        and (exc.status_code is None or exc.status_code >= 500)
    )


def _dataset_failure_kind(exc: BumpaProviderError) -> DatasetFailureKind:
    if exc.failure_kind == "timeout":
        return "timeout"
    if exc.failure_kind == "transport":
        return "transport"
    return "upstream_http"


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


def _metric_value(payload: dict[str, Any]) -> Decimal | None:
    candidates: list[Any] = []
    for container in (payload, payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in (
            "value",
            "amount",
            "total",
            "count",
            "total_sales",
            "gross_profit",
            "net_profit",
        ):
            if key in container:
                candidates.append(container[key])
    return next((value for raw in candidates if (value := parse_money(raw)) is not None), None)


def _pagination(payload: dict[str, Any], requested_page: int) -> tuple[int, int]:
    raw = payload.get("pagination") or payload.get("meta") or {}
    if not isinstance(raw, dict):
        return requested_page, requested_page
    current = _nonnegative_int(str(raw.get("current_page", requested_page))) or requested_page
    last = _nonnegative_int(str(raw.get("last_page", current))) or current
    return current, max(current, last)


def _normalise_order(payload: dict[str, Any]) -> ProviderOrder:
    identifier = payload.get("id") or payload.get("order_id") or payload.get("uuid")
    if identifier is None:
        raise BumpaProviderError("Bumpa order is missing an identifier")
    currency = str(payload.get("currency") or payload.get("currency_code") or "NGN").upper()
    if len(currency) != 3:
        currency = "NGN"
    order_date = _parse_datetime(
        payload.get("order_date") or payload.get("created_at") or payload.get("date")
    )
    return ProviderOrder(
        order_id=str(identifier),
        order_number=str(payload.get("order_number") or payload.get("number") or identifier),
        status=str(payload.get("status") or "unknown"),
        payment_status=str(payload.get("payment_status") or "unknown"),
        currency_code=currency,
        # Missing or malformed money stays unavailable. Treating it as zero would
        # silently turn an incomplete provider record into a real commercial fact.
        total_amount=parse_money(
            payload["total_amount"] if "total_amount" in payload else payload.get("total")
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
