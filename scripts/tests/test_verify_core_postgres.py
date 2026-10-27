from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).parents[1] / "verify_core_postgres.py"
SPEC = importlib.util.spec_from_file_location("disposable_postgres_gate", PATH)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class RetentionGateTest(unittest.TestCase):
    def test_rejects_remote_or_unowned_database(self) -> None:
        for url in (
            "postgresql+psycopg://synthetic_admin:test@remote.example.com/bumpa_core_synthetic",
            "postgresql+psycopg://synthetic_admin:test@127.0.0.1/production",
            "postgresql+psycopg://real_user:test@127.0.0.1/bumpa_core_synthetic",
        ):
            with self.subTest(url=url), patch.object(GATE.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "owned disposable"):
                    GATE._run_retention_gate(url, {})
                run.assert_not_called()

    def test_skip_is_not_counted_as_a_successful_postgres_test(self) -> None:
        with (
            patch.object(
                GATE.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "1 skipped\n", ""),
            ),
            self.assertRaisesRegex(RuntimeError, "did not pass"),
        ):
            GATE._run_retention_gate(
                "postgresql+psycopg://synthetic_admin:test@127.0.0.1/bumpa_core_synthetic",
                {},
            )

    def test_exact_owned_url_is_passed_only_in_the_explicit_retention_environment(
        self,
    ) -> None:
        url = "postgresql+psycopg://synthetic_admin:synthetic@127.0.0.1:54321/bumpa_core_synthetic"
        with patch.object(
            GATE.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "1 passed in 0.5s\n", ""),
        ) as run:
            GATE._run_retention_gate(url, {"APP_ENV": "test"})
        args, options = run.call_args
        self.assertEqual(options["env"]["POSTGRES_RETENTION_TEST_DATABASE_URL"], url)
        self.assertEqual(options["env"]["APP_ENV"], "test")
        self.assertEqual(
            args[0][-1],
            "tests/test_operational_retention.py::test_postgres_overlapping_retention_batches_are_disjoint_and_drain",
        )
        self.assertNotIn(url, args[0])


if __name__ == "__main__":
    unittest.main()
