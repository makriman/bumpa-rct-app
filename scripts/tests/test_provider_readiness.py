from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "provider_readiness.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"


class ProviderReadinessContractTests(unittest.TestCase):
    def selector(self, backend: str, primary_sender_enabled: str) -> subprocess.CompletedProcess[str]:
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

    def test_primary_meta_and_disabled_selectors_match_api_contract(self) -> None:
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

    def test_invalid_selector_inputs_fail_closed(self) -> None:
        for inputs in (("mock", "true"), ("meta", "yes"), ("", "false")):
            with self.subTest(inputs=inputs):
                result = self.selector(*inputs)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_deploy_gate_uses_derived_selector(self) -> None:
        deploy = DEPLOY.read_text(encoding="utf-8")
        self.assertIn('source "$ROOT_DIR/scripts/provider_readiness.sh"', deploy)
        imported_keys = deploy.split("for key in \\\n", 1)[1].split("; do", 1)[0]
        self.assertIn("WHATSAPP_BACKEND", imported_keys)
        self.assertIn("META_PRIMARY_SENDER_ENABLED", imported_keys)
        self.assertIn(
            '"$WHATSAPP_BACKEND" "$META_PRIMARY_SENDER_ENABLED"',
            deploy,
        )
        self.assertIn('--arg whatsapp "$expected_whatsapp"', deploy)
        self.assertNotIn('--arg whatsapp "$WHATSAPP_BACKEND"', deploy)


if __name__ == "__main__":
    unittest.main()
