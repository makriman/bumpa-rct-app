"""Server-owned evidence for canonical order publication across currencies."""

from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement


def publication_marker_predicate(run: Any) -> ColumnElement[bool]:
    marker = run.dataset_results["_bumpa_order_publication"]
    return and_(
        marker["version"].as_integer().in_((1, 2)),
        marker["state"].as_string() == "complete",
    )


def publication_marker_valid(run: Any) -> bool:
    marker = (run.dataset_results or {}).get("_bumpa_order_publication")
    return (
        isinstance(marker, dict)
        and type(marker.get("version")) is int
        and marker.get("version") in (1, 2)
        and marker.get("state") == "complete"
    )


def published_currency_predicate(order: Any, run: Any, connection: Any) -> ColumnElement[bool]:
    """Legacy publications admit only their base currency; v2 stamps each row."""
    return or_(
        order.currency_code == connection.store_currency,
        and_(
            run.dataset_results["_bumpa_order_publication"]["version"].as_integer() == 2,
            order.raw_payload["_bumpa_publication"]["currency_code"].as_string()
            == order.currency_code,
        ),
    )
