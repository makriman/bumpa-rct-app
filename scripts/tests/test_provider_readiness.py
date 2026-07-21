from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "provider_readiness.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"


class ProviderReadinessContractTests(unittest.TestCase):
    def selector(
        self, backend: str, primary_sender_enabled: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; expected_whatsapp_readiness_selector "$2" "$3"',
                "provider-readiness-test",
                str(HELPER),
                backend,
                primary_sender_enabled,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_selectors_match_the_api_contract(self) -> None:
        expected = {
            ("disabled", "true"): "disabled",
            ("disabled", "false"): "disabled",
            ("meta", "true"): "meta",
            ("meta", "false"): "meta_test_reply_only",
        }
        for inputs, selector in expected.items():
            with self.subTest(inputs=inputs):
                result = self.selector(*inputs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), selector)

    def test_invalid_inputs_fail_closed(self) -> None:
        for inputs in (("mock", "true"), ("meta", "yes"), ("", "false")):
            with self.subTest(inputs=inputs):
                result = self.selector(*inputs)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def readiness_matches(
        self, payload: str, whatsapp: str, bumpa: str = "bumpa", agent: str = "hermes"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; provider_readiness_matches "$2" "$3" "$4" "$5"',
                "provider-readiness-test",
                str(HELPER),
                payload,
                whatsapp,
                bumpa,
                agent,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_exact_phase_readiness_payloads(self) -> None:
        disabled = (
            '{"status":"ready","database":"ok","providers":'
            '{"whatsapp":"disabled","bumpa":"bumpa","agent":"hermes"}}'
        )
        reply_only = (
            '{"status":"ready","database":"ok","providers":'
            '{"whatsapp":"meta_test_reply_only","bumpa":"bumpa","agent":"hermes"}}'
        )
        self.assertEqual(self.readiness_matches(disabled, "disabled").returncode, 0)
        self.assertEqual(
            self.readiness_matches(reply_only, "meta_test_reply_only").returncode,
            0,
        )
        self.assertNotEqual(self.readiness_matches(reply_only, "meta").returncode, 0)
        self.assertNotEqual(
            self.readiness_matches(reply_only, "meta_test_reply_only", bumpa="disabled").returncode,
            0,
        )
        self.assertNotEqual(
            self.readiness_matches(reply_only, "meta_test_reply_only", agent="disabled").returncode,
            0,
        )

    def test_deploy_loads_and_uses_the_disambiguating_selector(self) -> None:
        deploy = DEPLOY.read_text(encoding="utf-8")
        self.assertIn('source "$ROOT_DIR/scripts/provider_readiness.sh"', deploy)
        extraction = deploy.split("for key in \\\n", maxsplit=1)[1].split("; do", maxsplit=1)[0]
        self.assertIn("META_PRIMARY_SENDER_ENABLED", extraction)
        self.assertIn(
            '"$WHATSAPP_BACKEND" "$META_PRIMARY_SENDER_ENABLED"',
            deploy,
        )
        self.assertIn(
            '"$ready_payload" "$expected_whatsapp" "$BUMPA_BACKEND" "$AGENT_BACKEND"',
            deploy,
        )
        self.assertNotIn('--arg whatsapp "$WHATSAPP_BACKEND"', deploy)
        derivation = deploy.index('expected_whatsapp="$(')
        forward_boundary = deploy.index(
            'write_promotion_state "$promotion_state_file" FORWARD_BOUNDARY'
        )
        self.assertLess(derivation, forward_boundary)
        self.assertIn("--connect-timeout 10 --max-time 20", deploy)


if __name__ == "__main__":
    unittest.main()
