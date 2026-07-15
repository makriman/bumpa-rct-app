from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


class ProductionBoundaryDefaultsTest(unittest.TestCase):
    def test_api_base_uses_the_branded_production_boundary(self) -> None:
        self.assertEqual(helper.DEFAULT_API_BASE, "https://api.bumpabestie.com/v1")


class ProductionInputContextTest(unittest.TestCase):
    def _secret_file(self, directory: str, *, omit: str | None = None) -> Path:
        payloads = []
        mappings = []
        for index in range(1, 6):
            business_id = str(700000 + index)
            payload = {
                "secret_key": f"not-a-real-secret-{index}",
                "business_id": business_id,
                "store_timezone": "Africa/Lagos",
                "store_currency": "NGN",
            }
            if omit is not None:
                payload.pop(omit)
            payloads.append(payload)
            mappings.append(f"{business_id} = +23480000000{index}")
        path = Path(directory) / "inputs.md"
        path.write_text(
            "OPERATOR_PHONE_E164=+234800000001\n\n"
            + helper.json.dumps(payloads, indent=2)
            + "\n\n"
            + "\n".join(mappings)
            + "\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
        return path

    def test_requires_explicit_store_timezone_and_currency_for_every_store(
        self,
    ) -> None:
        for omitted, expected in (
            ("store_timezone", "bumpa_store_timezone_missing_or_invalid"),
            ("store_currency", "bumpa_store_currency_missing_or_invalid"),
        ):
            with (
                self.subTest(omitted=omitted),
                tempfile.TemporaryDirectory() as directory,
            ):
                with self.assertRaisesRegex(helper.OpsError, expected):
                    helper._read_inputs(
                        self._secret_file(directory, omit=omitted),
                        allow_operator_owner_overlap=True,
                    )

    def test_preserves_explicit_normalized_store_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inputs = helper._read_inputs(
                self._secret_file(directory), allow_operator_owner_overlap=True
            )
        self.assertEqual(
            {(store.store_timezone, store.store_currency) for store in inputs.stores},
            {("Africa/Lagos", "NGN")},
        )


class StoreContextPropagationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = helper.Store(
            index=1,
            api_key="test-key-not-a-real-secret",
            business_id="business-ke-1",
            owner_phone="+254700000001",
            store_timezone="Africa/Nairobi",
            store_currency="KES",
        )
        self.inputs = helper.Inputs(
            operator_phone="+254700000001",
            stores=(self.store, self.store, self.store, self.store, self.store),
        )

    def test_onboarding_bundle_carries_explicit_store_context(self) -> None:
        bundle = helper._onboard_bundle(self.inputs, self.store, apply=False)

        self.assertEqual(bundle["bumpa"]["store_timezone"], "Africa/Nairobi")
        self.assertEqual(bundle["bumpa"]["store_currency"], "KES")

    def test_onboarding_audit_carries_explicit_store_context(self) -> None:
        captured: dict[str, object] = {}

        def fake_remote(
            _host: str, _program: str, stdin: bytes, _timeout: int = 60
        ) -> bytes:
            captured.update(helper.json.loads(stdin))
            return helper.json.dumps(
                {
                    "status": "ok",
                    "tenants": 5,
                    "owners": 5,
                    "owner_memberships": 5,
                    "phone_identities": 5,
                    "bumpa_connections": 5,
                    "dual_role_count": 1,
                }
            ).encode()

        with mock.patch.object(helper, "_remote_api_python", side_effect=fake_remote):
            helper.audit_onboarding(self.inputs, "root@example.test")

        stores = captured["stores"]
        self.assertIsInstance(stores, list)
        assert isinstance(stores, list)
        self.assertEqual(stores[0]["store_timezone"], "Africa/Nairobi")
        self.assertEqual(stores[0]["store_currency"], "KES")


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
            (RUN_ID, "success", 10, 0, 0),
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
        self.assertEqual(result, (RUN_ID, "partial", 8, 2, 0))

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
                self.assertEqual(result, (RUN_ID, "partial", 9, 1, 0))

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

    def test_rejects_typed_dataset_error_even_when_run_is_explicitly_degraded(
        self,
    ) -> None:
        with self.assertRaisesRegex(helper.OpsError, "dataset_error"):
            helper._validate_completed_sync_run(
                _run(
                    status="partial",
                    completion_quality="degraded",
                    partial_reason="dataset_error",
                    overrides={
                        "products.overview": "error",
                        "sales.gross_profit": "unavailable",
                        "sales.net_profit": "unavailable",
                    },
                )
            )

    def test_accepts_only_an_explicitly_named_typed_dataset_error(self) -> None:
        result = helper._validate_completed_sync_run(
            _run(
                status="partial",
                completion_quality="degraded",
                partial_reason="dataset_error",
                overrides={
                    "products.overview": "error",
                    "sales.gross_profit": "unavailable",
                    "sales.net_profit": "unavailable",
                },
            ),
            allowed_dataset_errors=frozenset({"products.overview"}),
        )

        self.assertEqual(result, (RUN_ID, "partial", 7, 2, 1))

    def test_release_error_allowance_is_scoped_to_store_five_products_overview(
        self,
    ) -> None:
        self.assertEqual(
            helper._parse_dataset_error_allowance("5:products.overview"),
            (5, "products.overview"),
        )
        for invalid in ("1:products.overview", "5:sales.overview", "products.overview"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(helper.argparse.ArgumentTypeError),
            ):
                helper._parse_dataset_error_allowance(invalid)

    def test_allowed_error_requires_exact_durable_transient_upstream_evidence(
        self,
    ) -> None:
        for failure_kind, http_status in (("timeout", None), ("upstream_http", 504)):
            with (
                self.subTest(failure_kind=failure_kind),
                mock.patch.object(
                    helper,
                    "_remote_api_python",
                    return_value=helper.json.dumps(
                        {
                            "rows": [
                                {
                                    "dataset": "products.overview",
                                    "http_status": http_status,
                                    "availability": "error",
                                    "failure_kind": failure_kind,
                                }
                            ]
                        }
                    ).encode(),
                ),
            ):
                helper._validate_allowed_dataset_error_evidence(
                    "root@example.test",
                    tenant_id="tenant-test",
                    run_id=RUN_ID,
                    expected_errors=frozenset({"products.overview"}),
                )

        for failure_kind, http_status in (
            ("invalid_response", 200),
            ("transport", None),
            ("upstream_http", 401),
        ):
            with (
                self.subTest(failure_kind=failure_kind),
                mock.patch.object(
                    helper,
                    "_remote_api_python",
                    return_value=helper.json.dumps(
                        {
                            "rows": [
                                {
                                    "dataset": "products.overview",
                                    "http_status": http_status,
                                    "availability": "error",
                                    "failure_kind": failure_kind,
                                }
                            ]
                        }
                    ).encode(),
                ),
                self.assertRaisesRegex(helper.OpsError, "error_evidence_invalid"),
            ):
                helper._validate_allowed_dataset_error_evidence(
                    "root@example.test",
                    tenant_id="tenant-test",
                    run_id=RUN_ID,
                    expected_errors=frozenset({"products.overview"}),
                )

    def test_rejects_untyped_dataset_error_completion_evidence(self) -> None:
        with self.assertRaisesRegex(helper.OpsError, "dataset_error"):
            helper._validate_completed_sync_run(
                _run(status="partial", overrides={"products.overview": "error"})
            )

    def test_rejects_all_ten_dataset_errors_instead_of_greenlighting_empty_data(
        self,
    ) -> None:
        with self.assertRaisesRegex(helper.OpsError, "dataset_error"):
            helper._validate_completed_sync_run(
                _run(
                    status="partial",
                    completion_quality="degraded",
                    partial_reason="dataset_error",
                    overrides={
                        dataset: "error" for dataset in helper.EXPECTED_SYNC_DATASETS
                    },
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
            helper._validate_completed_sync_run(_run(completion_quality="legacy"))


class ApiRequestTest(unittest.TestCase):
    def test_pins_explicit_ops_user_agent_on_every_request(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"{}"

        class FakeOpener:
            def __init__(self) -> None:
                self.requests = []

            def open(self, request, *, timeout):
                self.requests.append((request, timeout))
                return FakeResponse()

        opener = FakeOpener()
        with mock.patch.object(
            helper.urllib.request, "build_opener", return_value=opener
        ):
            helper._api_request(
                "https://api.example.test/v1",
                "GET",
                "/admin/tenants",
                "session-token",
            )
            helper._api_request(
                "https://api.example.test/v1",
                "POST",
                "/bumpa/sync/latest",
                "session-token",
                payload={"canary": True},
                headers={
                    "Idempotency-Key": "production-canary",
                    "User-Agent": "caller-supplied-signature",
                },
            )

        self.assertEqual(len(opener.requests), 2)
        for request, timeout in opener.requests:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertEqual(headers["user-agent"], helper.OPS_USER_AGENT)
            self.assertEqual(timeout, 45)


if __name__ == "__main__":
    unittest.main()
