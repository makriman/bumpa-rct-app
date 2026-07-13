from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_runner():
    path = ROOT / "tests" / "load_failure" / "run.py"
    spec = importlib.util.spec_from_file_location("load_failure_runner_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("load/failure runner could not be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LoadFailureRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_disk_near_full_drill_sanitizes_and_signs_without_network(self) -> None:
        result = self.runner.run_disk_near_full_phase()

        self.assertEqual(result["source_exit_code"], 1)
        self.assertEqual(result["status"], "alert")
        self.assertEqual(result["block_used_percent"], 97)
        self.assertEqual(result["inode_used_percent"], 50)
        self.assertEqual(result["event_type"], "disk_capacity_failure")
        self.assertEqual(result["severity"], "critical")
        self.assertTrue(result["signature_verified"])
        self.assertTrue(result["idempotency_verified"])
        self.assertTrue(result["timestamp_verified"])
        self.assertTrue(result["sanitization_verified"])
        self.assertEqual(result["network_attempts"], 0)

    def test_status_counts_is_exact_and_json_safe(self) -> None:
        results = [
            self.runner.HttpResult(status=200, latency_ms=1.0, response={}),
            self.runner.HttpResult(status=429, latency_ms=2.0, response={}),
            self.runner.HttpResult(status=200, latency_ms=3.0, response={}),
        ]

        self.assertEqual(self.runner.status_counts(results), {"200": 2, "429": 1})

    def test_compose_project_scrubs_hostile_data_plane_environment(self) -> None:
        hostile = {
            "DATABASE_URL": "postgresql://live.example/production",
            "MIGRATION_DATABASE_URL": "postgresql://live.example/production",
            "REDIS_URL": "rediss://live.example/0",
            "POSTGRES_PASSWORD": "live-password",
            "JWT_SECRET": "live-jwt",
            "META_SYSTEM_USER_ACCESS_TOKEN": "live-meta-token",
            "META_SYSTEM_USER_ACCESS_TOKEN_FILE": "/run/secrets/live-meta",
            "PATH": os.environ.get("PATH", ""),
        }

        with patch.dict(os.environ, hostile, clear=True):
            project = self.runner.ComposeProject(port=18081)

        self.assertEqual(
            project.environment["DATABASE_URL"],
            self.runner.SYNTHETIC_COMPOSE_ENV["DATABASE_URL"],
        )
        self.assertEqual(project.environment["REDIS_URL"], "redis://redis:6379/0")
        self.assertEqual(project.environment["FIELD_ENCRYPTION_OLD_KEYS"], "{}")
        self.assertEqual(project.environment["META_SYSTEM_USER_ACCESS_TOKEN"], "")
        self.assertEqual(project.environment["META_SYSTEM_USER_ACCESS_TOKEN_FILE"], "")
        self.assertNotIn("live.example", repr(project.environment))
        self.assertNotIn("live-password", repr(project.environment))

    def test_compose_project_disables_ambient_compose_overrides(self) -> None:
        hostile = {
            "COMPOSE_FILE": "/hostile/live-compose.yaml",
            "COMPOSE_PROFILES": "production",
            "COMPOSE_ENV_FILES": "/hostile/live.env",
            "PATH": os.environ.get("PATH", ""),
        }

        with patch.dict(os.environ, hostile, clear=True):
            project = self.runner.ComposeProject(port=18082)

        self.assertEqual(project.environment["COMPOSE_DISABLE_ENV_FILE"], "1")
        self.assertNotIn("COMPOSE_FILE", project.environment)
        self.assertNotIn("COMPOSE_PROFILES", project.environment)
        self.assertNotIn("COMPOSE_ENV_FILES", project.environment)
        self.assertEqual(project.command[2:4], ["--env-file", os.devnull])

    def test_rendered_compose_rejects_live_provider_values(self) -> None:
        application = {
            "AGENT_BACKEND": "mock",
            "APP_ENV": "staging",
            "BUMPA_BACKEND": "mock",
            "DATABASE_URL": self.runner.SYNTHETIC_COMPOSE_ENV["DATABASE_URL"],
            "LOAD_FAILURE_FIXTURE_MODE": "true",
            "META_APP_SECRET": self.runner.FIXTURE_SECRET.decode(),
            "REDIS_URL": self.runner.SYNTHETIC_COMPOSE_ENV["REDIS_URL"],
            "WHATSAPP_BACKEND": "mock",
            **{key: "" for key in self.runner.EMPTY_PROVIDER_VALUES},
        }
        document = {
            "services": {
                "api": {"environment": dict(application)},
                "worker": {"environment": dict(application)},
                "scheduler": {"environment": dict(application)},
                "migrate": {
                    "environment": {
                        "MIGRATION_DATABASE_URL": self.runner.SYNTHETIC_COMPOSE_ENV[
                            "MIGRATION_DATABASE_URL"
                        ]
                    }
                },
                "postgres": {
                    "environment": {
                        key: self.runner.SYNTHETIC_COMPOSE_ENV[key]
                        for key in (
                            "APP_POSTGRES_PASSWORD",
                            "POSTGRES_DB",
                            "POSTGRES_PASSWORD",
                            "POSTGRES_USER",
                        )
                    }
                },
            }
        }
        self.runner.validate_rendered_compose(document)
        document["services"]["api"]["environment"]["META_SYSTEM_USER_ACCESS_TOKEN"] = "live-token"

        with self.assertRaises(self.runner.DrillFailure):
            self.runner.validate_rendered_compose(document)


if __name__ == "__main__":
    unittest.main()
