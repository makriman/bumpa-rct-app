from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BumpaConnection,
    BumpaMetricSnapshot,
    BumpaOrder,
    BumpaOrderItem,
    BumpaSyncRun,
    Tenant,
)

PeriodName = Literal[
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "last_30_days",
    "all_available",
    "custom",
]


@dataclass(frozen=True)
class ResolvedPeriod:
    name: str
    date_from: date
    date_to: date
    starts_at_utc: datetime
    ends_at_utc_exclusive: datetime
    timezone: str

    def payload(self) -> dict[str, str]:
        return {
            "period": self.name,
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "timezone": self.timezone,
        }


def tenant_store_context(db: Session, tenant_id: str) -> tuple[Tenant, str, str]:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("Tenant does not exist")
    connection = db.scalar(
        select(BumpaConnection)
        .where(
            BumpaConnection.tenant_id == tenant_id,
            BumpaConnection.status == "active",
        )
        .order_by(BumpaConnection.updated_at.desc())
    )
    timezone = connection.store_timezone if connection else tenant.timezone
    currency = connection.store_currency if connection else tenant.currency_code
    return tenant, timezone, currency


def resolve_period(
    db: Session,
    *,
    tenant_id: str,
    period: PeriodName | str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> ResolvedPeriod:
    _tenant, timezone_name, _currency = tenant_store_context(db, tenant_id)
    timezone = ZoneInfo(timezone_name)
    current = (now or datetime.now(UTC)).astimezone(timezone)
    today = current.date()

    if period == "custom":
        if date_from is None or date_to is None:
            raise ValueError("Custom periods require date_from and date_to")
        start, end = date_from, date_to
    elif period == "today":
        start = end = today
    elif period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "this_week":
        start = today - timedelta(days=today.weekday())
        end = today
    elif period == "last_week":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
    elif period == "this_month":
        start = today.replace(day=1)
        end = today
    elif period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif period == "last_30_days":
        end = today
        start = today - timedelta(days=29)
    elif period == "all_available":
        bounds = db.execute(
            select(func.min(BumpaOrder.order_date), func.max(BumpaOrder.order_date)).where(
                BumpaOrder.tenant_id == tenant_id,
                BumpaOrder.order_date.is_not(None),
            )
        ).one()
        start = _as_local_date(bounds[0], timezone) if bounds[0] else today
        end = _as_local_date(bounds[1], timezone) if bounds[1] else today
    else:
        raise ValueError("Unsupported period")

    if end < start:
        raise ValueError("date_to must be on or after date_from")
    if (end - start).days > 366:
        raise ValueError("Periods cannot exceed 367 days")
    starts_at = datetime.combine(start, time.min, timezone).astimezone(UTC)
    ends_at = datetime.combine(end + timedelta(days=1), time.min, timezone).astimezone(UTC)
    return ResolvedPeriod(
        name=period,
        date_from=start,
        date_to=end,
        starts_at_utc=starts_at,
        ends_at_utc_exclusive=ends_at,
        timezone=timezone_name,
    )


def business_profile(db: Session, tenant_id: str) -> dict[str, Any]:
    tenant, timezone, currency = tenant_store_context(db, tenant_id)
    return {
        "business": {
            "name": tenant.name,
            "category": tenant.business_category,
            "country": tenant.country,
            "city": tenant.city,
        },
        "store": {"timezone": timezone, "currency": currency},
        "source": "Bumpa Bestie tenant profile",
    }


def data_coverage(db: Session, tenant_id: str) -> dict[str, Any]:
    _tenant, timezone, currency = tenant_store_context(db, tenant_id)
    latest = db.scalar(
        select(BumpaSyncRun)
        .where(BumpaSyncRun.tenant_id == tenant_id)
        .order_by(BumpaSyncRun.finished_at.desc().nullslast(), BumpaSyncRun.started_at.desc())
    )
    order_bounds = db.execute(
        select(
            func.min(BumpaOrder.order_date),
            func.max(BumpaOrder.order_date),
            func.count(BumpaOrder.id),
        ).where(BumpaOrder.tenant_id == tenant_id)
    ).one()
    zone = ZoneInfo(timezone)
    dataset_results = latest.dataset_results if latest is not None else {}
    unavailable = sorted(
        key
        for key, value in dataset_results.items()
        if isinstance(value, dict) and value.get("availability") != "available"
    )
    return {
        "timezone": timezone,
        "currency": currency,
        "orders": {
            "count": int(order_bounds[2] or 0),
            "date_from": (
                _as_local_date(order_bounds[0], zone).isoformat() if order_bounds[0] else None
            ),
            "date_to": (
                _as_local_date(order_bounds[1], zone).isoformat() if order_bounds[1] else None
            ),
        },
        "latest_sync": {
            "status": latest.status if latest else "unavailable",
            "quality": latest.completion_quality if latest else None,
            "finished_at": (
                _as_utc(latest.finished_at).isoformat() if latest and latest.finished_at else None
            ),
            "requested_from": latest.requested_from.isoformat() if latest else None,
            "requested_to": latest.requested_to.isoformat() if latest else None,
            "unavailable_datasets": unavailable,
        },
        "warnings": (["No completed Bumpa sync is available."] if latest is None else []),
    }


def business_overview(
    db: Session,
    tenant_id: str,
    *,
    period: PeriodName | str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    compare_to: Literal["previous_period", "none"] = "previous_period",
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_period(
        db,
        tenant_id=tenant_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        now=now,
    )
    current = _overview_for_resolved_period(db, tenant_id, resolved)
    comparison: dict[str, Any] | None = None
    if compare_to == "previous_period":
        days = (resolved.date_to - resolved.date_from).days + 1
        previous_to = resolved.date_from - timedelta(days=1)
        previous_from = previous_to - timedelta(days=days - 1)
        previous = resolve_period(
            db,
            tenant_id=tenant_id,
            period="custom",
            date_from=previous_from,
            date_to=previous_to,
            now=now,
        )
        prior = _overview_for_resolved_period(db, tenant_id, previous)
        comparison = {
            "period": previous.payload(),
            "sales_change": _decimal_change(current["sales_total"], prior["sales_total"]),
            "order_count_change": _integer_change(
                current["order_count"],
                prior["order_count"],
            ),
        }
    return {
        "source": "Bumpa canonical orders",
        "period": resolved.payload(),
        "currency": current.pop("currency"),
        "metrics": current,
        "comparison": comparison,
        "inclusion_rules": [
            "Orders whose order_date falls inside the exact store-local period.",
            "Sales total sums available order total_amount values.",
            "Missing amounts are excluded and reported; cancelled/refunded orders remain visible.",
        ],
        "freshness": data_coverage(db, tenant_id)["latest_sync"],
    }


def sales_trend(
    db: Session,
    tenant_id: str,
    *,
    period: PeriodName | str = "this_month",
    granularity: Literal["day", "week", "month"] = "day",
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_period(
        db,
        tenant_id=tenant_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        now=now,
    )
    _tenant, timezone_name, currency = tenant_store_context(db, tenant_id)
    timezone = ZoneInfo(timezone_name)
    rows = db.execute(
        select(BumpaOrder.order_date, BumpaOrder.total_amount).where(
            BumpaOrder.tenant_id == tenant_id,
            BumpaOrder.order_date >= resolved.starts_at_utc,
            BumpaOrder.order_date < resolved.ends_at_utc_exclusive,
        )
    ).all()
    buckets: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"sales_total": Decimal("0"), "order_count": 0, "missing_amount_count": 0}
    )
    for ordered_at, amount in rows:
        if ordered_at is None:
            continue
        local_day = _as_utc(ordered_at).astimezone(timezone).date()
        if granularity == "week":
            key = local_day - timedelta(days=local_day.weekday())
        elif granularity == "month":
            key = local_day.replace(day=1)
        else:
            key = local_day
        buckets[key]["order_count"] += 1
        if amount is None:
            buckets[key]["missing_amount_count"] += 1
        else:
            buckets[key]["sales_total"] += amount
    points = [
        {
            "period_start": key.isoformat(),
            "sales_total": str(value["sales_total"]),
            "order_count": value["order_count"],
            "missing_amount_count": value["missing_amount_count"],
        }
        for key, value in sorted(buckets.items())
    ]
    return {
        "source": "Bumpa canonical orders",
        "period": resolved.payload(),
        "granularity": granularity,
        "currency": currency,
        "points": points,
        "row_count": len(rows),
        "truncated": False,
    }


def order_breakdown(
    db: Session,
    tenant_id: str,
    *,
    period: PeriodName | str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_period(
        db,
        tenant_id=tenant_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        now=now,
    )
    orders = db.scalars(
        select(BumpaOrder).where(
            BumpaOrder.tenant_id == tenant_id,
            BumpaOrder.order_date >= resolved.starts_at_utc,
            BumpaOrder.order_date < resolved.ends_at_utc_exclusive,
        )
    ).all()
    return {
        "source": "Bumpa canonical orders",
        "period": resolved.payload(),
        "order_count": len(orders),
        "by_status": _count_values(order.status for order in orders),
        "by_payment_status": _count_values(order.payment_status for order in orders),
        "by_channel": _count_values(order.channel for order in orders),
        "by_origin": _count_values(order.origin for order in orders),
    }


def product_performance(
    db: Session,
    tenant_id: str,
    *,
    period: PeriodName | str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_period(
        db,
        tenant_id=tenant_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        now=now,
    )
    _tenant, _timezone, currency = tenant_store_context(db, tenant_id)
    safe_limit = min(max(limit, 1), 25)
    rows = db.execute(
        select(
            BumpaOrderItem.product_id,
            BumpaOrderItem.name,
            func.sum(BumpaOrderItem.quantity),
            func.sum(BumpaOrderItem.total_amount),
            func.count(func.distinct(BumpaOrderItem.order_id)),
        )
        .join(BumpaOrder, BumpaOrder.id == BumpaOrderItem.order_id)
        .where(
            BumpaOrderItem.tenant_id == tenant_id,
            BumpaOrder.tenant_id == tenant_id,
            BumpaOrder.order_date >= resolved.starts_at_utc,
            BumpaOrder.order_date < resolved.ends_at_utc_exclusive,
        )
        .group_by(BumpaOrderItem.product_id, BumpaOrderItem.name)
        .order_by(func.sum(BumpaOrderItem.total_amount).desc().nullslast())
        .limit(safe_limit)
    ).all()
    return {
        "source": "Bumpa canonical order items",
        "period": resolved.payload(),
        "currency": currency,
        "products": [
            {
                "product_id": product_id,
                "name": name or "Unnamed product",
                "quantity": str(quantity) if quantity is not None else None,
                "sales_total": str(total) if total is not None else None,
                "order_count": int(order_count),
            }
            for product_id, name, quantity, total, order_count in rows
        ],
        "limit": safe_limit,
    }


def customer_summary(
    db: Session,
    tenant_id: str,
    *,
    period: PeriodName | str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_period(
        db,
        tenant_id=tenant_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        now=now,
    )
    return {
        "source": "Bumpa canonical data",
        "period": resolved.payload(),
        "availability": "unavailable",
        "warnings": [
            "Canonical orders do not currently contain a privacy-safe customer identifier, "
            "so repeat-customer counts and segments cannot be calculated reliably."
        ],
    }


def inventory_overview(db: Session, tenant_id: str) -> dict[str, Any]:
    snapshot = db.scalar(
        select(BumpaMetricSnapshot)
        .where(
            BumpaMetricSnapshot.tenant_id == tenant_id,
            BumpaMetricSnapshot.metric_key.in_(
                (
                    "products.inventory",
                    "products.inventory_value",
                    "products.low_stock",
                    "products.out_of_stock",
                )
            ),
        )
        .order_by(BumpaMetricSnapshot.created_at.desc())
    )
    if snapshot is None:
        return {
            "source": "Bumpa metrics",
            "availability": "unavailable",
            "warnings": ["The current Bumpa sync does not expose normalized inventory data."],
        }
    return {
        "source": "Bumpa metrics",
        "availability": snapshot.availability,
        "metric_key": snapshot.metric_key,
        "value": str(snapshot.value_decimal) if snapshot.value_decimal is not None else None,
        "details": snapshot.canonical_payload,
        "requested_from": snapshot.requested_from.isoformat(),
        "requested_to": snapshot.requested_to.isoformat(),
    }


def exact_calculation(expression: str) -> dict[str, str]:
    if len(expression) > 500:
        raise ValueError("Expression is too long")
    try:
        parsed = ast.parse(expression, mode="eval")
        value = _evaluate_decimal(parsed.body)
    except (SyntaxError, InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError("Expression is invalid") from exc
    return {"expression": expression, "result": format(value.normalize(), "f")}


def _overview_for_resolved_period(
    db: Session,
    tenant_id: str,
    period: ResolvedPeriod,
) -> dict[str, Any]:
    _tenant, _timezone, currency = tenant_store_context(db, tenant_id)
    rows = db.execute(
        select(
            func.count(BumpaOrder.id),
            func.sum(BumpaOrder.total_amount),
            func.count(BumpaOrder.total_amount),
            func.count(func.distinct(BumpaOrder.channel)),
        ).where(
            BumpaOrder.tenant_id == tenant_id,
            BumpaOrder.order_date >= period.starts_at_utc,
            BumpaOrder.order_date < period.ends_at_utc_exclusive,
        )
    ).one()
    order_count = int(rows[0] or 0)
    amount_count = int(rows[2] or 0)
    return {
        "currency": currency,
        "sales_total": str(rows[1]) if rows[1] is not None else None,
        "order_count": order_count,
        "orders_with_amount": amount_count,
        "orders_missing_amount": order_count - amount_count,
        "distinct_channels": int(rows[3] or 0),
        "gross_profit": None,
        "net_profit": None,
        "profit_warning": (
            "Profit is unavailable for arbitrary periods because normalized cost data is absent."
        ),
    }


def _as_local_date(value: datetime, timezone: ZoneInfo) -> date:
    return _as_utc(value).astimezone(timezone).date()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _count_values(values: Any) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value or "unknown"] += 1
    return [
        {"value": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _decimal_change(current: str | None, previous: str | None) -> dict[str, str | None]:
    if current is None or previous is None:
        return {"absolute": None, "percent": None}
    current_value, previous_value = Decimal(current), Decimal(previous)
    absolute = current_value - previous_value
    percent = None if previous_value == 0 else (absolute / previous_value * Decimal("100"))
    return {
        "absolute": format(absolute.normalize(), "f"),
        "percent": format(percent.quantize(Decimal("0.01")), "f") if percent is not None else None,
    }


def _integer_change(current: int, previous: int) -> dict[str, int | str | None]:
    absolute = current - previous
    percent = None if previous == 0 else Decimal(absolute) / Decimal(previous) * Decimal("100")
    return {
        "absolute": absolute,
        "percent": format(percent.quantize(Decimal("0.01")), "f") if percent is not None else None,
    }


def _evaluate_decimal(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_decimal(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _evaluate_decimal(node.left), _evaluate_decimal(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            if right != right.to_integral_value() or abs(right) > 12:
                raise ValueError("Exponent is outside the supported range")
            return left ** int(right)
    raise ValueError("Expression contains unsupported operations")
