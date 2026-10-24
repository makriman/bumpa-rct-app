import importlib.util
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "preserved_auth", ROOT / "scripts/validate_preserved_auth.py"
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
IMAGE = "sha256:" + "c" * 64


class PreservedAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_path = self.root / ".env.production"
        self.record_path = self.root / ".deployed-release.json"
        self.env = {
            "AUTH_LOGIN_MODE": "temporary_static_pin",
            "TEMPORARY_WEB_PIN_VERIFIER": "",
            "TEMPORARY_WEB_PIN_VERIFIER_FILE": "/run/auth-secret/temporary_web_pin_verifier",
            "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST": "/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier",
            "TEMPORARY_WEB_PIN_EXPIRES_AT": "2026-09-16T23:59:59Z",
            "WHATSAPP_BACKEND": "meta",
            "META_PRIMARY_SENDER_ENABLED": "true",
            "PROACTIVE_INSIGHTS_ENABLED": "false",
            "JWT_SECRET": "synthetic-only-test-value",
            "SESSION_COOKIE_SECURE": "true",
            "DEPLOY_REF": "a" * 40,
            "API_IMAGE": "registry.example/api@sha256:" + "a" * 64,
        }
        self.write_env()
        self.record = {
            "auth": {
                "login_mode": self.env["AUTH_LOGIN_MODE"],
                "temporary_web_pin_verifier_file": self.env[
                    "TEMPORARY_WEB_PIN_VERIFIER_FILE"
                ],
                "temporary_web_pin_verifier_file_host": self.env[
                    "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"
                ],
                "temporary_web_pin_expires_at": self.env[
                    "TEMPORARY_WEB_PIN_EXPIRES_AT"
                ],
                "whatsapp_backend": "meta",
            },
            "preserved_configuration": {
                "schema_version": 1,
                "policy": "unchanged-expired-pin-no-new-logins",
                "running_api_image_id": IMAGE,
                "non_release_environment_sha256": module.configuration_digest(
                    self.env_path.read_bytes()
                ),
            },
        }
        self.write_record()

    def write_env(self):
        self.env_path.write_text(
            "# Preserve every nonrelease byte\n"
            + "".join(f"{k}={v}\n" for k, v in self.env.items())
        )
        self.env_path.chmod(0o600)

    def write_record(self):
        self.record_path.write_text(json.dumps(self.record))
        self.record_path.chmod(0o600)

    def test_exact_expired_boundary_accepts_release_pointer_changes_only(self):
        module.validate(self.env_path, self.record_path, IMAGE)
        self.env["DEPLOY_REF"] = "b" * 40
        self.env["API_IMAGE"] = "registry.example/api@sha256:" + "b" * 64
        self.write_env()
        module.validate(self.env_path, self.record_path, IMAGE)

    def test_each_auth_or_other_nonrelease_change_is_rejected(self):
        original = self.env.copy()
        for key in original:
            if key in module.RELEASE_KEYS:
                continue
            with self.subTest(key=key):
                self.env = {**original, key: original[key] + "-changed"}
                self.write_env()
                with self.assertRaises(ValueError):
                    module.validate(self.env_path, self.record_path, IMAGE)

    def test_naive_malformed_and_new_future_expiry_are_never_preservation(self):
        for expiry in ["yesterday", "2020-01-01T00:00:00", "2099-01-01T00:00:00Z"]:
            with self.subTest(expiry=expiry):
                self.env["TEMPORARY_WEB_PIN_EXPIRES_AT"] = expiry
                self.write_env()
                self.record["auth"]["temporary_web_pin_expires_at"] = expiry
                self.record["preserved_configuration"][
                    "non_release_environment_sha256"
                ] = module.configuration_digest(self.env_path.read_bytes())
                self.write_record()
                with self.assertRaises(ValueError):
                    module.validate(self.env_path, self.record_path, IMAGE)

    def test_missing_legacy_or_malformed_proof_and_image_mismatch_fail(self):
        for record in [
            {},
            {"auth": {}},
            {**self.record, "preserved_configuration": []},
        ]:
            self.record_path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                module.validate(self.env_path, self.record_path, IMAGE)
        self.write_record()
        with self.assertRaises(ValueError):
            module.validate(self.env_path, self.record_path, "sha256:" + "d" * 64)

    def test_unsafe_record_mode_or_symlink_fails(self):
        self.record_path.chmod(0o644)
        with self.assertRaises(ValueError):
            module.validate(self.env_path, self.record_path, IMAGE)
        self.record_path.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(self.record_path)
        with self.assertRaises(ValueError):
            module.validate(self.env_path, link, IMAGE)

    def test_deploy_orders_proof_after_identity_and_before_side_effects(self):
        source = (ROOT / "scripts/deploy.sh").read_text()
        guard = source.index(
            "Running $service image does not match the verified release boundary"
        )
        proof = source.index("--preserve-recorded-auth .deployed-release.json")
        pull = source.index('"${compose[@]}" --profile tools pull caddy postgres redis')
        self.assertLess(guard, proof)
        self.assertLess(proof, pull)
        self.assertIn('"$previous_release_file_sha256"', source[guard:proof])

    def test_captured_snapshot_removed_after_preflight_aborts_instead_of_skipping(self):
        source = (ROOT / "scripts/deploy.sh").read_text()
        start = source.index(
            'if [[ -n "$pilot_snapshot_sha256" ]]; then',
            source.index('"${compose[@]}" --profile tools run --rm migrate'),
        )
        end = source.index(
            '\n"${compose[@]}" --profile tools run --rm --no-deps', start
        )
        script = (
            'set -eu\npilot_snapshot="$MISSING_SNAPSHOT"\npilot_snapshot_sha256=captured\ncompose=(false)\n'
            + source[start:end]
        )
        result = subprocess.run(
            ["/bin/bash"],
            input=script,
            env={**os.environ, "MISSING_SNAPSHOT": str(self.root / "missing")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("scope changed before application", result.stderr)

    def test_conditional_persistence_fails_closed_on_docker_inspect_error_or_invalid_id(
        self,
    ):
        source = (ROOT / "scripts/deploy.sh").read_text()
        function = source[
            source.index("persist_release_metadata() {") : source.index(
                "\ndeployment_started=0"
            )
        ]
        ref = "registry.example/api@sha256:" + "a" * 64
        args = (
            ["a" * 40, "sha-" + "a" * 40, "sha-" + "b" * 40]
            + [ref] * 11
            + [
                "a" * 40,
                "temporary_static_pin",
                self.env["TEMPORARY_WEB_PIN_VERIFIER_FILE"],
                self.env["TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"],
                self.env["TEMPORARY_WEB_PIN_EXPIRES_AT"],
                "meta",
            ]
        )
        for stub in ["return 1", "printf 'invalid-id\\n'"]:
            script = (
                "set -u\nvalidated_preserved_digest=validated\nvalidate_release_pointer_values() { return 0; }\nvalidate_auth_boundary_values() { return 0; }\ndocker() { "
                + stub
                + "; }\n"
                + function
                + "\nif persist_release_metadata "
                + shlex.join(args)
                + "; then exit 99; else exit 0; fi\n"
            )
            result = subprocess.run(
                ["/bin/bash"],
                input=script,
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(self.record_path.read_text()), self.record)
            self.assertEqual(list(self.root.glob("*.tmp.*")), [])


if __name__ == "__main__":
    unittest.main()
