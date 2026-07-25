from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "agent_capability_status.sh"


class AgentCapabilityStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.tavily = self.secret("tavily", "tavily-canary")
        self.elevenlabs = self.secret("elevenlabs", "elevenlabs-canary")
        self.sandbox = self.secret("sandbox", "sandbox-canary")
        self.google = self.secret("google", "google-canary")
        self.meta_ads = self.secret("meta-ads", "meta-ads-canary")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def secret(self, name: str, value: str) -> Path:
        path = self.root / name
        path.write_text(value, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def environment(self, **overrides: str) -> Path:
        values = {
            "AGENT_CAPABILITIES_V2": "true",
            "HERMES_TOOLS_ENABLED": "true",
            "WEB_RESEARCH_ENABLED": "false",
            "SANDBOX_TOOLS_ENABLED": "true",
            "MANAGED_IMAGE_GENERATION_ENABLED": "true",
            "EXTERNAL_CONNECTORS_ENABLED": "false",
            "MCP_GOOGLE_OAUTH_ENABLED": "false",
            "MCP_META_ADS_OAUTH_ENABLED": "false",
            "WHATSAPP_PRIMARY_PILOT_ENABLED": "true",
            "WHATSAPP_MULTIMODAL_ENABLED": "true",
            "WHATSAPP_SPEECH_ENABLED": "false",
            "WHATSAPP_PROGRESS_ENABLED": "true",
            "PROACTIVE_INSIGHTS_ENABLED": "false",
            "DAILY_INSIGHTS_ENABLED": "false",
            "WEEKLY_INSIGHTS_ENABLED": "false",
            "TAVILY_API_KEY_FILE_HOST": str(self.tavily),
            "ELEVENLABS_API_KEY_FILE_HOST": str(self.elevenlabs),
            "ELEVENLABS_TTS_VOICE_ID": "voice_canary",
            "SANDBOX_SERVICE_TOKEN_FILE_HOST": str(self.sandbox),
            "SANDBOX_WORKER_URL": "https://sandbox.example.test",
            "GOOGLE_OAUTH_CLIENT_ID": "google-client",
            "GOOGLE_OAUTH_CLIENT_SECRET_FILE_HOST": str(self.google),
            "META_ADS_OAUTH_CLIENT_ID": "123456789",
            "META_ADS_OAUTH_CLIENT_SECRET_FILE_HOST": str(self.meta_ads),
            "AUTH_LOGIN_MODE": "temporary_static_pin",
        }
        values.update(overrides)
        path = self.root / "environment"
        path.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        return path

    def run_helper(self, environment: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(HELPER), str(environment)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_reports_active_ready_and_disabled_states_without_secret_leakage(self) -> None:
        result = self.run_helper(self.environment())

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agent"]["consultant"], "active")
        self.assertEqual(payload["research"]["state"], "ready_disabled")
        self.assertEqual(payload["sandbox"]["state"], "active")
        self.assertEqual(
            payload["sandbox"]["managed_image_generation"],
            "active",
        )
        self.assertEqual(payload["connectors"]["google_oauth"], "ready_disabled")
        self.assertEqual(payload["connectors"]["meta_ads_oauth"], "ready_disabled")
        self.assertEqual(payload["whatsapp"]["speech"], "ready_disabled")
        self.assertEqual(payload["whatsapp"]["otp"], "disabled")
        self.assertNotIn(str(self.root), result.stdout)
        self.assertNotIn("canary", result.stdout)

    def test_missing_prerequisites_and_enabled_misconfiguration_are_explicit(self) -> None:
        missing = self.root / "missing"
        result = self.run_helper(
            self.environment(
                WEB_RESEARCH_ENABLED="true",
                WHATSAPP_SPEECH_ENABLED="true",
                TAVILY_API_KEY_FILE_HOST=str(missing),
                ELEVENLABS_API_KEY_FILE_HOST=str(missing),
                ELEVENLABS_TTS_VOICE_ID="",
                GOOGLE_OAUTH_CLIENT_ID="",
                GOOGLE_OAUTH_CLIENT_SECRET_FILE_HOST=str(missing),
                META_ADS_OAUTH_CLIENT_ID="",
                META_ADS_OAUTH_CLIENT_SECRET_FILE_HOST=str(missing),
            )
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["research"]["state"], "misconfigured")
        self.assertEqual(payload["whatsapp"]["speech"], "misconfigured")
        self.assertEqual(
            payload["connectors"]["google_oauth"],
            "blocked_missing_prerequisite",
        )
        self.assertEqual(
            payload["connectors"]["meta_ads_oauth"],
            "blocked_missing_prerequisite",
        )

    def test_duplicate_or_invalid_environment_values_fail_closed(self) -> None:
        environment = self.environment(AGENT_CAPABILITIES_V2="yes")
        invalid = self.run_helper(environment)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")

        with environment.open("a", encoding="utf-8") as handle:
            handle.write("WEB_RESEARCH_ENABLED=false\n")
        duplicate = self.run_helper(environment)
        self.assertEqual(duplicate.returncode, 2)
        self.assertEqual(duplicate.stdout, "")

    def test_multiline_secret_is_not_ready_even_without_a_final_newline(self) -> None:
        self.tavily.write_text("first-line\nsecond-line", encoding="utf-8")
        result = self.run_helper(self.environment())

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["research"]["tavily_credential_ready"])
        self.assertEqual(
            payload["research"]["state"],
            "blocked_missing_prerequisite",
        )


if __name__ == "__main__":
    unittest.main()
