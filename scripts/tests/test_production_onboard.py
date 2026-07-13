from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_helper():
    path = Path(__file__).resolve().parents[1] / "production_onboard.py"
    spec = importlib.util.spec_from_file_location("production_onboard_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("production onboarding helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper = _load_helper()
RUN_ID = "11111111-1111-4111-8111-111111111111"


def _run(
    *,
    status: str = "success",
    completion_quality: str = "complete",
    partial_reason: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict:
    datasets = {key: "available" for key in helper.EXPECTED_SYNC_DATASETS}
    datasets.update(overrides or {})
    return {
        "id": RUN_ID,
        "status": status,
        "completion_quality": completion_quality,
        "partial_reason": partial_reason,
        "orders_availability": "available",
        "orders_count": 0,
        "dataset_results": datasets,
        "error": None,
    }


class SyncCanaryValidationTest(unittest.TestCase):
    def test_accepts_complete_success(self) -> None:
        self.assertEqual(
            helper._validate_completed_sync_run(_run()),
            (RUN_ID, "success", 10, 0),
        )

    def test_accepts_only_provider_unavailable_profit_metrics_as_partial(self) -> None:
        result = helper._validate_completed_sync_run(
            _run(
                status="partial",
                completion_quality="accepted_partial",
                partial_reason="profit_not_calculable",
                overrides={
                    "sales.gross_profit": "unavailable",
                    "sales.net_profit": "unavailable",
                },
            )
        )
        self.assertEqual(result, (RUN_ID, "partial", 8, 2))

    def test_accepts_each_single_provider_approved_profit_limitation(self) -> None:
        for dataset in helper.OPTIONAL_UNAVAILABLE_SYNC_DATASETS:
            with self.subTest(dataset=dataset):
                result = helper._validate_completed_sync_run(
                    _run(
                        status="partial",
                        completion_quality="accepted_partial",
                        partial_reason="profit_not_calculable",
                        overrides={dataset: "unavailable"},
                    )
                )
                self.assertEqual(result, (RUN_ID, "partial", 9, 1))

    def test_rejects_unavailable_required_metric(self) -> None:
        with self.assertRaisesRegex(helper.OpsError, "required_dataset_unavailable"):
            helper._validate_completed_sync_run(
                _run(
                    status="partial",
                    completion_quality="degraded",
                    partial_reason="dataset_unavailable",
                    overrides={"sales.overview": "unavailable"},
                )
            )

    def test_rejects_error_status_even_for_optional_metric(self) -> None:
        with self.assertRaisesRegex(helper.OpsError, "dataset_status_invalid"):
            helper._validate_completed_sync_run(
                _run(
                    status="partial",
                    completion_quality="degraded",
                    partial_reason="dataset_error",
                    overrides={"sales.gross_profit": "error"},
                )
            )

    def test_rejects_missing_or_unexpected_dataset(self) -> None:
        missing = _run()
        missing["dataset_results"].pop("sales.overview")
        with self.assertRaisesRegex(helper.OpsError, "dataset_set_invalid"):
            helper._validate_completed_sync_run(missing)

    def test_rejects_partial_status_when_analytics_are_all_available(self) -> None:
        with self.assertRaisesRegex(helper.OpsError, "run_status_mismatch"):
            helper._validate_completed_sync_run(
                _run(
                    status="partial",
                    completion_quality="degraded",
                    partial_reason="orders_unavailable",
                )
            )

    def test_rejects_orders_failure_even_with_approved_profit_unavailability(
        self,
    ) -> None:
        run = _run(
            status="partial",
            completion_quality="degraded",
            partial_reason="orders_unavailable",
            overrides={"sales.gross_profit": "unavailable"},
        )
        run["orders_availability"] = "error"
        run["orders_count"] = None
        with self.assertRaisesRegex(helper.OpsError, "orders_unavailable"):
            helper._validate_completed_sync_run(run)

    def test_rejects_missing_or_invalid_orders_evidence(self) -> None:
        for value in (None, -1, True, "0"):
            with self.subTest(value=value):
                run = _run()
                run["orders_count"] = value
                with self.assertRaisesRegex(helper.OpsError, "orders_count_invalid"):
                    helper._validate_completed_sync_run(run)

    def test_rejects_non_null_run_error(self) -> None:
        run = _run()
        run["error"] = "sanitized failure"
        with self.assertRaisesRegex(helper.OpsError, "run_error_present"):
            helper._validate_completed_sync_run(run)

    def test_rejects_untrusted_profit_partial_quality_or_reason(self) -> None:
        for quality, reason in (
            ("degraded", "dataset_unavailable"),
            ("accepted_partial", "dataset_unavailable"),
            ("complete", None),
        ):
            with self.subTest(quality=quality, reason=reason):
                with self.assertRaisesRegex(
                    helper.OpsError, "completion_evidence_invalid"
                ):
                    helper._validate_completed_sync_run(
                        _run(
                            status="partial",
                            completion_quality=quality,
                            partial_reason=reason,
                            overrides={"sales.net_profit": "unavailable"},
                        )
                    )

    def test_rejects_success_with_partial_completion_evidence(self) -> None:
        with self.assertRaisesRegex(helper.OpsError, "completion_evidence_invalid"):
            helper._validate_completed_sync_run(
                _run(
                    completion_quality="accepted_partial",
                    partial_reason="profit_not_calculable",
                )
            )

    def test_rejects_legacy_run_as_unverified(self) -> None:
        with self.assertRaisesRegex(helper.OpsError, "completion_evidence_invalid"):
            helper._validate_completed_sync_run(
                _run(completion_quality="legacy")
            )


if __name__ == "__main__":
    unittest.main()
