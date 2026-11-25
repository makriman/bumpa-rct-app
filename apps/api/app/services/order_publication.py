"""Server-owned evidence for canonical order publication across currencies."""

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import BumpaConnection


def publication_marker_predicate(run: Any) -> ColumnElement[bool]:
    marker = run.dataset_results["_bumpa_order_publication"]
    local_legacy = (
        select(BumpaConnection.id)
        .where(
            BumpaConnection.id == run.bumpa_connection_id,
            BumpaConnection.tenant_id == run.tenant_id,
            BumpaConnection.provider == "local",
        )
        .correlate(run)
        .exists()
    )
    return and_(
        marker["state"].as_string() == "complete",
        or_(
            and_(marker["version"].as_integer().in_((1, 2)), local_legacy),
            and_(
                marker["version"].as_integer() == 3,
                marker["range_contract"].as_string() == "full_catalog_order_date_v1",
                marker["catalog_count"].as_integer() >= run.orders_count,
                marker["catalog_count"].as_integer() >= 0,
            ),
        ),
    )


def publication_marker_valid(run: Any) -> bool:
    marker = (run.dataset_results or {}).get("_bumpa_order_publication")
    if (
        not isinstance(marker, dict)
        or type(marker.get("version")) is not int
        or marker.get("state") != "complete"
    ):
        return False
    if marker["version"] in (1, 2):
        return True  # SQL callers additionally require the deterministic local provider.
    return (
        marker["version"] == 3
        and marker.get("range_contract") == "full_catalog_order_date_v1"
        and type(marker.get("catalog_count")) is int
        and run.orders_count is not None
        and marker["catalog_count"] >= run.orders_count >= 0
    )


def published_currency_predicate(order: Any, run: Any, connection: Any) -> ColumnElement[bool]:
    """Legacy publications admit only their base currency; v2 stamps each row."""
    return or_(
        order.currency_code == connection.store_currency,
        and_(
            run.dataset_results["_bumpa_order_publication"]["version"].as_integer().in_((2, 3)),
            order.raw_payload["_bumpa_publication"]["currency_code"].as_string()
            == order.currency_code,
        ),
    )
