from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderDataset:
    resource: str
    dataset: str
    availability: str
    payload: dict[str, Any]
    value: Decimal | None = None
    title: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProviderOrder:
    order_id: str
    order_number: str
    status: str
    payment_status: str
    currency_code: str
    total_amount: Decimal
    order_date: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class BumpaSnapshot:
    datasets: list[ProviderDataset]
    orders: list[ProviderOrder]


class CommerceProvider(Protocol):
    def sync(self, date_from: date, date_to: date) -> BumpaSnapshot: ...


class MessagingProvider(Protocol):
    def send_text(self, phone_e164: str, body: str) -> str: ...

    def send_otp(self, phone_e164: str, code: str) -> str: ...


class AgentRuntime(Protocol):
    def respond(self, profile_name: str, message: str, business_context: str) -> str: ...


class Classifier(Protocol):
    def classify(self, message: str, data_used: str) -> dict[str, str]: ...


class ArtifactStore(Protocol):
    def put(self, key: str, content: bytes) -> tuple[str, int, str]: ...

    def get(self, key: str) -> bytes: ...
