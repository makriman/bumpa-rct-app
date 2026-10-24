import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "scripts/deploy.sh").read_text()
VERIFY = re.search(
    r"(?ms)^verify_cached_rollback_images\(\) \{.*?^\}\n", SOURCE
).group()
CALLBACK = re.search(
    r"(?ms)^    attempt_application_rollback\(\) \{.*?^    \}\n", SOURCE
).group()
REF = "registry.example/prior@sha256:" + "a" * 64
IMAGE = "sha256:" + "b" * 64


class CachedRollbackTests(unittest.TestCase):
    def run_callback(self, image_output, docker_status=0):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "calls"
            env = {
                **os.environ,
                "IMAGE_OUTPUT": image_output,
                "DOCKER_STATUS": str(docker_status),
                "CALL_LOG": str(log),
                "REF": REF,
            }
            harness = r"""
set -u
docker() { printf '%s\n' "$IMAGE_OUTPUT"; return "$DOCKER_STATUS"; }
rewrite_release_boundary() { return 0; }
run_production_smoke() { return 0; }
compose_stub() {
  printf '%s\n' "$*" >>"$CALL_LOG"
  [[ " $* " == *" --pull never "* ]] || return 98
  [[ " $* " != *" pull "* ]] || return 97
}
compose=(compose_stub)
previous_hermes_running=1
previous_worker_running=1
previous_scheduler_running=1
previous_revision=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
previous_image_tag=sha-$previous_revision
target_infra_image_tag=$previous_image_tag
previous_api_image=$REF
previous_web_image=$REF
rollback_admin_web_image=$REF
rollback_research_web_image=$REF
target_caddy_image=$REF
target_postgres_image=$REF
target_backup_image=$REF
previous_hermes_image=$REF
previous_auth_login_mode=temporary_static_pin
previous_temporary_web_pin_verifier_file=/run/auth-secret/temporary_web_pin_verifier
previous_temporary_web_pin_verifier_file_host=/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier
previous_temporary_web_pin_expires_at=2020-01-01T00:00:00Z
previous_whatsapp_backend=meta
target_api_image=$REF
rollback_image_refs=("$REF")
rollback_services=(api web admin-web research-web caddy hermes worker scheduler)
"""
            result = subprocess.run(
                ["/bin/bash"],
                input=harness
                + VERIFY
                + CALLBACK
                + "\nif attempt_application_rollback; then exit 0; else exit 42; fi\n",
                env=env,
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            return (
                result.returncode,
                log.read_text().splitlines() if log.exists() else [],
            )

    def test_all_callback_recreations_use_verified_cache_without_registry(self):
        status, calls = self.run_callback(IMAGE)
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("--pull never" in call for call in calls))

    def test_missing_cache_fails_in_conditional_context_before_recreate(self):
        status, calls = self.run_callback("", docker_status=1)
        self.assertEqual(status, 42)
        self.assertEqual(calls, [])

    def test_invalid_cached_identity_fails_before_recreate(self):
        status, calls = self.run_callback("not-an-immutable-image")
        self.assertEqual(status, 42)
        self.assertEqual(calls, [])

    def test_cache_set_includes_initializer_and_edge(self):
        self.assertIn(
            '"${rollback_image_refs[@]}" "$target_api_image" "$target_caddy_image"',
            CALLBACK,
        )
        self.assertNotIn('"${compose[@]}" pull', CALLBACK)


if __name__ == "__main__":
    unittest.main()
