from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.providers.contracts import (
    ArtifactStore,
    BumpaSnapshot,
    ProviderDataset,
    ProviderOrder,
)

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


class LocalCommerceProvider:
    """Predictable commerce fixture with all required datasets and paginated-like orders."""

    def __init__(self, tenant_seed: str) -> None:
        self._seed = int(hashlib.sha256(tenant_seed.encode()).hexdigest()[:6], 16)

    def sync(self, date_from: date, date_to: date) -> BumpaSnapshot:
        days = Decimal((date_to - date_from).days + 1)
        baseline = Decimal(100_000 + self._seed % 200_000)
        values = {
            "sales.overview": baseline * days,
            "sales.total_sales": baseline * days,
            "sales.gross_profit": baseline * days * Decimal("0.31"),
            "sales.net_profit": baseline * days * Decimal("0.19"),
            "products.overview": Decimal(24),
            "products.products_sold": days * Decimal(7),
            "products.top_selling_products": Decimal(18),
            "products.least_selling_products": Decimal(2),
            "customers.overview": days * Decimal(3),
            "customers.top_customers_order": Decimal(8),
        }
        datasets: list[ProviderDataset] = []
        for resource, names in DATASETS.items():
            for name in names:
                key = f"{resource}.{name}"
                value = values[key]
                datasets.append(
                    ProviderDataset(
                        resource=resource,
                        dataset=name,
                        availability="available",
                        value=value,
                        title=key.replace("_", " ").title(),
                        payload={"dataset": name, "value": str(value), "currency": "NGN"},
                    )
                )
        orders = [
            ProviderOrder(
                order_id=f"local-{self._seed}-{index}",
                order_number=f"BB-{1000 + index}",
                status="completed" if index < 4 else "pending",
                payment_status="paid" if index < 4 else "unpaid",
                currency_code="NGN",
                total_amount=Decimal(15_000 + index * 3_250),
                order_date=datetime.combine(date_to, datetime.min.time(), tzinfo=UTC)
                - timedelta(hours=index * 4),
                payload={
                    "id": f"local-{self._seed}-{index}",
                    "customer_details": {"name": f"Customer {index}", "phone": "+234000000000"},
                    "shipping_details": {"address": "[fixture address]"},
                    "total": str(15_000 + index * 3_250),
                },
            )
            for index in range(6)
        ]
        return BumpaSnapshot(datasets=datasets, orders=orders)


class LocalMessagingProvider:
    def send_text(self, phone_e164: str, body: str) -> str:
        digest = hashlib.sha256(f"{phone_e164}:{body}".encode()).hexdigest()[:20]
        return f"local-msg-{digest}"

    def send_otp(self, phone_e164: str, code: str) -> str:
        return self.send_text(phone_e164, f"Your Bumpa Bestie code is {code}")


class LocalAgentRuntime:
    def respond(self, profile_name: str, message: str, business_context: str) -> str:
        if "api_key" in business_context.lower() or "secret" in business_context.lower():
            raise ValueError("Business context contains forbidden credential material")
        lower = message.lower()
        if any(word in lower for word in ("sales", "sold", "revenue")):
            return (
                "Your latest synced business data is ready. "
                f"{business_context} Based on that picture, focus first on the strongest products "
                "and compare them with the slowest sellers before restocking."
            )
        return (
            "I’m your Bumpa Bestie. I can help with sales, products, customers and orders. "
            f"For this answer I used the following safe business summary: {business_context}"
        )


class LocalClassifier:
    def classify(self, message: str, data_used: str) -> dict[str, str]:
        text = message.lower()
        if any(word in text for word in ("stock", "inventory", "restock")):
            intent, function = "inventory_management", "stock"
        elif any(word in text for word in ("customer", "buyer")):
            intent, function = "customer_management", "customers"
        elif any(word in text for word in ("sales", "sold", "revenue", "profit")):
            intent, function = "sales_analysis", "sales"
        elif any(word in text for word in ("order", "delivery")):
            intent, function = "order_management", "fulfillment"
        else:
            intent, function = "general_business_advice", "strategy"
        return {
            "primary_intent": intent,
            "business_function": function,
            "ai_help_type": "data_lookup" if data_used != "none" else "recommendation",
            "complexity": "single_step_reasoning",
            "bumpa_data_used": data_used,
            "classification_version": "local-rules-v1",
        }


class LocalArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root not in candidate.parents:
            raise ValueError("Invalid artifact key")
        return candidate

    def put(self, key: str, content: bytes) -> tuple[str, int, str]:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return key, len(content), hashlib.sha256(content).hexdigest()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


def local_profile_key() -> str:
    return f"local-{uuid4()}"
