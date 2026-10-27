from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.run_reconciliation import (
    DeliveryOutcome,
    DeliveryReceipt,
    DurableModelOutput,
    FrozenRequest,
    ModelOutcome,
    ReconciliationAction,
    RunSnapshot,
    delivery_outcome,
    freeze_request,
    reconcile_run,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def snapshot(**changes):
    base = RunSnapshot(
        run_id="app-run",
        initiating_message_id="inbound-one",
        status="running",
        expires_at=NOW - timedelta(seconds=1),
        provider_run_ids=("provider_one",),
        request_snapshot=freeze_request(
            {"model": "synthetic-profile", "input": "hello", "idempotency_key": "app-run"}
        ),
    )
    return replace(base, **changes)


def completed(run_id="provider_one", **changes):
    return {"run_id": run_id, "status": "completed", "output": "Accepted answer", **changes}


def must_not_poll(_run_id):
    pytest.fail("Provider lookup must not occur")


def durable_output(**changes):
    return replace(
        DurableModelOutput("outbound-one", "app-run", "inbound-one", "Accepted answer"), **changes
    )


def test_freeze_detaches_exact_request_and_has_verifiable_digest():
    payload = {"input": [{"content": "hello"}], "history": [], "model": "synthetic-profile"}
    frozen = freeze_request(payload)
    payload["input"][0]["content"] = "later mutation"
    assert frozen.is_valid()
    assert "later mutation" not in frozen.payload_json
    assert replace(frozen, payload_json=frozen.payload_json + " ").is_valid() is False
    assert replace(frozen, sha256="0" * 64).is_valid() is False
    with pytest.raises(FrozenInstanceError):
        frozen.sha256 = "0" * 64


@pytest.mark.parametrize("frozen", [FrozenRequest("[]", "0" * 64), FrozenRequest("bad", "bad")])
def test_invalid_snapshot_with_known_completion_cannot_publish(frozen):
    result = reconcile_run(snapshot(request_snapshot=frozen), now=NOW, poll=lambda _: completed())
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.COMPLETED
    assert result.output is None


def test_unexpired_application_run_cannot_be_reconciled_or_polled():
    result = reconcile_run(
        snapshot(expires_at=NOW + timedelta(seconds=1)), now=NOW, poll=must_not_poll
    )
    assert result.action == ReconciliationAction.WAIT


def test_expiry_boundary_and_naive_database_utc_are_consistent():
    local_now = NOW.astimezone(timezone(timedelta(hours=1)))
    result = reconcile_run(
        snapshot(expires_at=NOW.replace(tzinfo=None)), now=local_now, poll=lambda _: completed()
    )
    assert result.action == ReconciliationAction.PERSIST_OUTPUT


def test_missing_admission_never_reconstructs_or_resubmits_request():
    result = reconcile_run(snapshot(provider_run_ids=()), now=NOW, poll=must_not_poll)
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.UNKNOWN
    assert result.reason == "provider_admission_unknown"


@pytest.mark.parametrize("run_id", ["../other-profile", "", "x" * 161, "a/b", 17])
def test_untrusted_recorded_identifier_never_reaches_poll(run_id):
    result = reconcile_run(snapshot(provider_run_ids=(run_id,)), now=NOW, poll=must_not_poll)
    assert result.reason == "provider_identity_invalid"


def test_duplicate_admissions_require_review_without_guessing_final_attempt():
    result = reconcile_run(
        snapshot(provider_run_ids=("provider_one", "provider_one")), now=NOW, poll=must_not_poll
    )
    assert result.reason == "provider_admission_duplicate"


def test_completed_known_admission_recovers_output_but_not_delivery_success():
    calls = []

    def poll(run_id):
        calls.append(run_id)
        return completed(
            usage={"input_tokens": 2, "completion_tokens": 3}, session_id="native-session"
        )

    result = reconcile_run(snapshot(), now=NOW, poll=poll)
    assert calls == ["provider_one"]
    assert result.action == ReconciliationAction.PERSIST_OUTPUT
    assert result.model_outcome == ModelOutcome.COMPLETED
    assert result.delivery_outcome == DeliveryOutcome.PENDING
    assert result.output.content == "Accepted answer"
    assert result.output.provider_run_id == "provider_one"
    assert result.output.total_tokens == 5
    assert result.output.session_id == "native-session"
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"


@pytest.mark.parametrize("state", ["queued", "running"])
def test_known_nonterminal_run_can_only_be_polled_later(state):
    result = reconcile_run(
        snapshot(), now=NOW, poll=lambda run_id: {"run_id": run_id, "status": state}
    )
    assert result.action == ReconciliationAction.POLL_LATER
    assert result.model_outcome == ModelOutcome.PENDING
    assert result.output is None


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_known_failure_does_not_reuse_draft_or_retry_external_effect(state):
    result = reconcile_run(
        snapshot(), now=NOW, poll=lambda run_id: {"run_id": run_id, "status": state}
    )
    assert result.action == ReconciliationAction.FAIL
    assert result.model_outcome == ModelOutcome.FAILED
    assert result.output is None


@pytest.mark.parametrize(
    "response,reason",
    [
        ([], "provider_response_invalid"),
        ({}, "provider_identity_mismatch"),
        (completed("different_run"), "provider_identity_mismatch"),
        ({"run_id": "provider_one", "status": "not_found"}, "provider_status_unknown"),
        ({"run_id": "provider_one", "status": []}, "provider_status_unknown"),
        ({"run_id": "provider_one", "status": "waiting_for_approval"}, "provider_status_unknown"),
        (completed(output=" "), "provider_output_invalid"),
        (completed(output=[]), "provider_output_invalid"),
        (completed(output="x" * 131_073), "provider_output_invalid"),
        (completed(usage=[]), "provider_output_invalid"),
        (completed(session_id=17), "provider_output_invalid"),
        (completed(session_id="x" * 161), "provider_output_invalid"),
    ],
)
def test_missing_misbound_or_malformed_outcome_is_unknown(response, reason):
    result = reconcile_run(snapshot(), now=NOW, poll=lambda _: response)
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.UNKNOWN
    assert result.reason == reason
    assert result.output is None


def test_poll_exception_remains_unknown_and_does_not_copy_private_exception():
    def poll(_run_id):
        raise RuntimeError("private credential or merchant text")

    result = reconcile_run(snapshot(), now=NOW, poll=poll)
    assert result.action == ReconciliationAction.POLL_LATER
    assert result.model_outcome == ModelOutcome.UNKNOWN
    assert "private" not in repr(result)


def test_legacy_digest_alone_cannot_authorize_recovered_output():
    result = reconcile_run(snapshot(request_snapshot=None), now=NOW, poll=lambda _: completed())
    assert result.action == ReconciliationAction.REVIEW
    assert result.reason == "frozen_request_missing_or_invalid"
    assert result.model_outcome == ModelOutcome.COMPLETED
    assert result.output is None


def test_last_admission_is_citation_repair_and_draft_is_never_recovered():
    calls = []

    def poll(run_id):
        calls.append(run_id)
        return completed(
            run_id,
            output="Verified https://official.example/a https://official.example/b"
            if run_id == "provider_repair"
            else "Unverified draft",
            usage={"input_tokens": 2, "output_tokens": 3},
        )

    result = reconcile_run(
        snapshot(provider_run_ids=("provider_one", "provider_repair"), minimum_citations=2),
        now=NOW,
        poll=poll,
    )
    assert calls == ["provider_one", "provider_repair"]
    assert result.output.provider_run_id == "provider_repair"
    assert result.output.content.startswith("Verified")
    assert result.output.input_tokens == 4
    assert result.output.output_tokens == 6
    assert result.output.total_tokens == 10
    assert "Unverified draft" not in repr(result)


def test_failed_repair_never_falls_back_to_successful_unpublished_draft():
    result = reconcile_run(
        snapshot(provider_run_ids=("provider_one", "provider_repair")),
        now=NOW,
        poll=lambda run_id: (
            completed(run_id)
            if run_id == "provider_one"
            else {"run_id": run_id, "status": "failed"}
        ),
    )
    assert result.action == ReconciliationAction.FAIL
    assert result.output is None


def test_unresolved_preceding_admission_does_not_establish_safe_final_output():
    result = reconcile_run(
        snapshot(provider_run_ids=("provider_one", "provider_repair")),
        now=NOW,
        poll=lambda run_id: (
            completed(run_id)
            if run_id == "provider_repair"
            else {"run_id": run_id, "status": "running"}
        ),
    )
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.UNKNOWN
    assert result.output is None


@pytest.mark.parametrize(
    "content", ["Unverified draft", "https://official.example/a https://official.example/a."]
)
def test_recovery_retains_citation_validation_without_auto_repair_submission(content):
    result = reconcile_run(
        snapshot(minimum_citations=2), now=NOW, poll=lambda _: completed(output=content)
    )
    assert result.action == ReconciliationAction.REVIEW
    assert result.reason == "output_validation_failed"
    assert result.output is None


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"run_id": "another-run"}, "durable_output_binding_invalid"),
        ({"initiating_message_id": "inbound-two"}, "durable_output_binding_invalid"),
        ({"content": ""}, "durable_output_binding_invalid"),
    ],
)
def test_durable_output_requires_exact_initiating_turn_binding(changes, reason):
    result = reconcile_run(
        snapshot(durable_output=durable_output(**changes)), now=NOW, poll=must_not_poll
    )
    assert result.action == ReconciliationAction.REVIEW
    assert result.reason == reason


def test_durable_output_can_prepare_delivery_without_rerunning_model():
    result = reconcile_run(
        snapshot(durable_output=durable_output(), request_snapshot=None),
        now=NOW,
        poll=must_not_poll,
    )
    assert result.action == ReconciliationAction.RESUME_DELIVERY
    assert result.model_outcome == ModelOutcome.COMPLETED
    assert result.output is None


def test_web_durable_output_is_complete_without_transport_delivery():
    result = reconcile_run(
        snapshot(durable_output=durable_output(), delivery_required=False),
        now=NOW,
        poll=must_not_poll,
    )
    assert result.action == ReconciliationAction.COMPLETE
    assert result.delivery_outcome == DeliveryOutcome.NOT_REQUIRED


@pytest.mark.parametrize("state", ["sending", "ambiguous", "future-state"])
def test_ambiguous_send_is_not_repeated_even_when_model_output_is_durable(state):
    result = reconcile_run(
        snapshot(
            durable_output=durable_output(),
            delivery_effect_keys=("reply:1",),
            deliveries=(DeliveryReceipt("reply:1", state),),
        ),
        now=NOW,
        poll=must_not_poll,
    )
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.COMPLETED
    assert result.delivery_outcome == DeliveryOutcome.UNKNOWN


def test_partial_chunk_delivery_resumes_only_remaining_prepared_effects():
    current = snapshot(
        durable_output=durable_output(),
        delivery_effect_keys=("reply:1", "reply:2", "reply:3"),
        deliveries=(
            DeliveryReceipt("reply:1", "sent", "meta-one"),
            DeliveryReceipt("reply:2", "prepared"),
        ),
    )
    result = reconcile_run(current, now=NOW, poll=must_not_poll)
    assert result.action == ReconciliationAction.RESUME_DELIVERY
    assert result.delivery_outcome == DeliveryOutcome.PENDING
    final = replace(
        current,
        deliveries=(
            DeliveryReceipt("reply:1", "read", "meta-one"),
            DeliveryReceipt("reply:2", "delivered", "meta-two"),
            DeliveryReceipt("reply:3", "sent", "meta-three"),
        ),
    )
    assert reconcile_run(final, now=NOW, poll=must_not_poll).action == ReconciliationAction.COMPLETE


@pytest.mark.parametrize(
    "keys,receipts",
    [
        (None, (DeliveryReceipt("reply:1", "sent", "meta-one"),)),
        ((), ()),
        (("reply:1", "reply:1"), ()),
        (("reply:1",), (DeliveryReceipt("reply:2", "sent", "meta-two"),)),
        (("reply:1",), (DeliveryReceipt("reply:1", "sent"),)),
        (("reply:1",), (DeliveryReceipt("reply:1", "failed", "meta-one"),)),
        (("reply:1",), (DeliveryReceipt("reply:1", "prepared", "meta-one"),)),
        (
            ("reply:1",),
            (
                DeliveryReceipt("reply:1", "sent", "meta-one"),
                DeliveryReceipt("reply:1", "sent", "meta-one"),
            ),
        ),
    ],
)
def test_missing_manifest_or_conflicting_receipts_never_establish_delivery(keys, receipts):
    assert (
        delivery_outcome(snapshot(delivery_effect_keys=keys, deliveries=receipts))
        == DeliveryOutcome.UNKNOWN
    )


def test_rejected_send_is_known_failure_but_not_safe_to_repeat():
    result = reconcile_run(
        snapshot(
            durable_output=durable_output(),
            delivery_effect_keys=("reply:1",),
            deliveries=(DeliveryReceipt("reply:1", "rejected"),),
        ),
        now=NOW,
        poll=must_not_poll,
    )
    assert result.action == ReconciliationAction.REVIEW
    assert result.delivery_outcome == DeliveryOutcome.FAILED


def test_model_recovery_does_not_erase_unknown_delivery():
    result = reconcile_run(
        snapshot(
            delivery_effect_keys=("reply:1",),
            deliveries=(DeliveryReceipt("reply:1", "sending"),),
        ),
        now=NOW,
        poll=lambda _: completed(),
    )
    assert result.action == ReconciliationAction.PERSIST_OUTPUT
    assert result.output.content == "Accepted answer"
    assert result.delivery_outcome == DeliveryOutcome.UNKNOWN


def test_provider_failure_does_not_erase_unknown_delivery():
    result = reconcile_run(
        snapshot(
            delivery_effect_keys=("reply:1",),
            deliveries=(DeliveryReceipt("reply:1", "sending"),),
        ),
        now=NOW,
        poll=lambda run_id: {"run_id": run_id, "status": "failed"},
    )
    assert result.action == ReconciliationAction.REVIEW
    assert result.model_outcome == ModelOutcome.FAILED
    assert result.delivery_outcome == DeliveryOutcome.UNKNOWN
