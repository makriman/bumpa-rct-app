from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "verify_hermes_mcp.py"
SPEC = importlib.util.spec_from_file_location("verify_hermes_mcp", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def private_url(
    *,
    scheme: str = "http",
    host: str = "api",
    port: int = 8000,
    path: str = "/internal/mcp/",
    suffix: str = "",
) -> str:
    return f"{scheme}://{host}:{port}{path}{suffix}"


class VerifyHermesMcpTests(unittest.TestCase):
    def test_private_mcp_url_is_exact(self) -> None:
        expected = private_url()
        self.assertEqual(MODULE.validate_private_mcp_url(expected), expected)
        for rejected in (
            private_url(scheme="https", host="api.bumpabestie.com", port=443),
            private_url(path="/internal/mcp"),
            private_url(host="postgres"),
            private_url(suffix="?tenant=other"),
            private_url(host="user:secret@api"),
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(MODULE.VerificationError):
                    MODULE.validate_private_mcp_url(rejected)

    def test_profile_discovery_and_key_parsing_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profiles = []
            for index in range(2):
                profile = root / f"tenant_store_{index}"
                profile.mkdir()
                environment = profile / ".env"
                environment.write_text(
                    "API_SERVER_ENABLED=true\n"
                    f"API_SERVER_KEY={'a' * 47}{index}\n",
                    encoding="utf-8",
                )
                os.chmod(environment, 0o600)
                profiles.append(profile)
            self.assertEqual(MODULE.discover_profiles(root, 2), profiles)
            self.assertEqual(MODULE.load_profile_key(profiles[0]), "a" * 47 + "0")
            with self.assertRaises(MODULE.VerificationError):
                MODULE.discover_profiles(root, 1)
            os.chmod(profiles[0] / ".env", 0o644)
            with self.assertRaises(MODULE.VerificationError):
                MODULE.load_profile_key(profiles[0])

    def test_required_surface_contains_business_research_and_sandbox_tools(self) -> None:
        self.assertEqual(len(MODULE.REQUIRED_TOOLS), 18)
        self.assertTrue(
            {
                "get_business_overview",
                "calculate_exactly",
                "research_web",
                "sandbox_exec",
                "generate_image",
                "prepare_connector_action",
            }.issubset(MODULE.REQUIRED_TOOLS)
        )


if __name__ == "__main__":
    unittest.main()
