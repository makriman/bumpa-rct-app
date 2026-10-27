from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import IngressStream, WebhookEvent
from app.jobs.runtime import JobDeferredError, PermanentJobError
from app.services.ingress_ordering import allocate_arrival, require_head


@pytest.fixture()
def engine(tmp_path):
    value = create_engine(f"sqlite:///{tmp_path / 'ordering.db'}", connect_args={"timeout": 15})
    Base.metadata.create_all(value)
    yield value
    value.dispose()


def payload(*, actor="2348000011111", waba="111", phone="222", timestamp="1"):
    return {
        "entry": [
            {
                "id": waba,
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone},
                            "messages": [
                                {"id": "provider-message", "from": actor, "timestamp": timestamp}
                            ],
                        }
                    }
                ],
            }
        ]
    }


def add_event(db, key, **changes):
    data = payload(**changes)
    event = WebhookEvent(
        provider="whatsapp", external_event_id=key, signature_valid=True, payload=data
    )
    db.add(event)
    db.flush()
    allocate_arrival(db, event, lane="production", payload=data)
    return event


def test_receipt_sequence_is_durable_and_ignores_provider_timestamp(engine):
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        first = add_event(db, "first", timestamp="9999999999")
        second = add_event(db, "second", timestamp="1")
        assert (first.arrival_sequence, second.arrival_sequence) == (1, 2)
        assert first.ordering_scope == second.ordering_scope
        db.commit()
        require_head(db, first)
        with pytest.raises(JobDeferredError):
            require_head(db, second)


def test_duplicate_keeps_first_receipt_position_and_does_not_advance_counter(engine):
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, "same")
        scope, sequence = event.ordering_scope, event.arrival_sequence
        allocate_arrival(db, event, lane="other", payload=payload(actor="2348000022222"))
        db.commit()
        assert (event.ordering_scope, event.arrival_sequence) == (scope, sequence)
        assert db.get(IngressStream, scope).next_sequence == 2


@pytest.mark.parametrize("status", ["received", "processing", "failed", "future_state"])
def test_unresolved_earlier_receipt_remains_barrier(engine, status):
    with Session(engine, expire_on_commit=False) as db:
        first = add_event(db, "first")
        second = add_event(db, "second")
        first.processing_status = status
        db.commit()
        with pytest.raises(JobDeferredError):
            require_head(db, second)


@pytest.mark.parametrize("status", ["processed", "ignored"])
def test_explicit_terminal_receipt_unblocks_next(engine, status):
    with Session(engine, expire_on_commit=False) as db:
        first = add_event(db, "first")
        second = add_event(db, "second")
        first.processing_status = status
        db.commit()
        require_head(db, second)


@pytest.mark.parametrize(
    "changes,lane",
    [
        ({"actor": "2348000022222"}, "production"),
        ({"waba": "333"}, "production"),
        ({"phone": "444"}, "production"),
        ({}, "staging"),
    ],
)
def test_actor_receiver_and_environment_have_independent_streams(engine, changes, lane):
    with Session(engine, expire_on_commit=False) as db:
        first = add_event(db, "first")
        other_payload = payload(**changes)
        other = WebhookEvent(
            provider="whatsapp",
            external_event_id="other",
            signature_valid=True,
            payload=other_payload,
        )
        db.add(other)
        allocate_arrival(db, other, lane=lane, payload=other_payload)
        db.commit()
        assert first.ordering_scope != other.ordering_scope
        assert other.arrival_sequence == 1
        require_head(db, other)


def test_sequence_allocation_rolls_back_with_its_receipt(engine):
    with Session(engine) as db:
        add_event(db, "rolled-back")
        db.rollback()
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, "durable")
        db.commit()
        assert event.arrival_sequence == 1
        assert len(db.scalars(select(WebhookEvent)).all()) == 1


def test_missing_or_invalid_receipt_order_fails_closed(engine):
    with Session(engine) as db:
        event = WebhookEvent(
            provider="whatsapp", external_event_id="legacy", signature_valid=True, payload=payload()
        )
        with pytest.raises(PermanentJobError, match="no durable arrival"):
            require_head(db, event)
        event.ordering_scope = "a" * 64
        with pytest.raises(PermanentJobError, match="incomplete"):
            allocate_arrival(db, event, lane="production", payload=payload())


def test_unknown_stream_and_unallocated_sequence_fail_closed(engine):
    with Session(engine, expire_on_commit=False) as db:
        event = add_event(db, "first")
        db.commit()
        event.arrival_sequence = 100
        db.flush()
        with pytest.raises(PermanentJobError, match="invalid arrival"):
            require_head(db, event)


def test_malformed_production_sender_and_delivery_events_are_not_ordered(engine):
    with Session(engine) as db:
        for index, data in enumerate(({}, payload(actor=""), payload(waba="invalid"))):
            event = WebhookEvent(
                provider="whatsapp",
                external_event_id=str(index),
                signature_valid=True,
                payload=data,
            )
            db.add(event)
            allocate_arrival(db, event, lane="production", payload=data)
            assert event.ordering_scope is None
            assert event.arrival_sequence is None


def test_concurrent_first_receipts_allocate_unique_committed_order(engine):
    def receive(index):
        with Session(engine, expire_on_commit=False, autoflush=False) as db:
            event = add_event(db, f"concurrent-{index}")
            db.commit()
            return event.arrival_sequence

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(receive, range(20)))
    assert sorted(sequences) == list(range(1, 21))
    with Session(engine) as db:
        events = db.scalars(select(WebhookEvent).order_by(WebhookEvent.arrival_sequence)).all()
        require_head(db, events[0])
        for event in events[1:]:
            with pytest.raises(JobDeferredError):
                require_head(db, event)
