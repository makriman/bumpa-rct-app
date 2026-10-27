"""Exercise the actual deploy rollback shell with a file-backed Docker double.

No daemon, provider, network, host configuration or production state is used.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "deploy.sh"
SOURCE = DEPLOY.read_text(encoding="utf-8")
EXECUTION_SERVICES = re.search(r"^canonical_execution_services=\([^\n]+\)$", SOURCE, re.M)
assert EXECUTION_SERVICES is not None
SERVICES = (
    "caddy", "api", "worker", "scheduler", "voice", "hermes",
    "web", "admin-web", "research-web", "postgres", "redis",
)
PREVIOUS_API = "example/api@sha256:" + "a" * 64
PREVIOUS_HERMES = "example/hermes@sha256:" + "b" * 64
TARGET_API = "example/api@sha256:" + "c" * 64
TARGET_HERMES = "example/hermes@sha256:" + "d" * 64
OTHER_IMAGE = "example/other@sha256:" + "e" * 64


def function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", SOURCE, re.M | re.S)
    if match is None:
        raise AssertionError(f"Missing deployment function: {name}")
    return match.group()


DOCKER_DOUBLE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FIXTURE"])
args = sys.argv[1:]
services = json.loads(os.environ["SERVICES"])
ids = {service: f"{index + 1:064x}" for index, service in enumerate(services)}
with (root / "commands").open("a") as log:
    log.write(json.dumps(args) + "\n")
def state(service):
    return root / "containers" / service
def selected_image(service):
    key = {"api":"API_IMAGE", "worker":"API_IMAGE", "scheduler":"API_IMAGE",
           "hermes":"HERMES_IMAGE", "web":"WEB_IMAGE", "admin-web":"ADMIN_WEB_IMAGE",
           "research-web":"RESEARCH_WEB_IMAGE", "caddy":"CADDY_IMAGE",
           "postgres":"POSTGRES_IMAGE"}.get(service)
    return os.environ.get(key, os.environ["OTHER_IMAGE"])
if args[:2] == ["image", "inspect"]:
    image = args[-1]
    if args[args.index("--format") + 1] == "{{.Id}}":
        if os.environ.get("FAIL_STAGE") == "cache":
            sys.exit(1)
        print("sha256:" + image.rsplit("@sha256:", 1)[1])
        sys.exit(0)
    labels = json.loads(os.environ["LABELS"])
    inspections = root / "label-inspections"
    inspected = json.loads(inspections.read_text()) if inspections.exists() else {}
    inspected[image] = inspected.get(image, 0) + 1
    inspections.write_text(json.dumps(inspected))
    if os.environ.get("FAIL_CONTRACT_RECHECK") == image and inspected[image] > 1:
        sys.exit(1)
    if labels.get(image) == "inspect-error":
        sys.exit(1)
    print(labels.get(image, "<no value>"))
elif args[0] == "ps":
    counter = root / "ps-count"
    count = int(counter.read_text()) + 1
    counter.write_text(str(count))
    if count == int(os.environ.get("FAIL_PS_CALL", "0")):
        sys.exit(1)
    service = next(x.rsplit("=", 1)[1] for x in args if x.startswith("label=com.docker.compose.service="))
    if state(service).exists():
        print(ids[service])
elif args[0] == "inspect":
    service = next((s for s in services if ids[s] == args[-1] and state(s).exists()), None)
    if service is None:
        sys.exit(1)
    if "--format" in args:
        print(json.loads(state(service).read_text())["image"])
elif args[0] == "rm":
    if os.environ.get("FAIL_DOCKER_RM") == "1":
        sys.exit(1)
    for service in services:
        if ids[service] in args:
            state(service).unlink(missing_ok=True)
elif args[0] == "start":
    for service in services:
        if ids[service] in args:
            state(service).write_text(json.dumps({"image":selected_image(service)}))
elif args[0] == "compose":
    command = next((x for x in args if x in ("ps", "logs", "pull", "up", "stop", "rm")), None)
    selected = [s for s in services if s in args]
    if command in ("stop", "rm") and os.environ.get("FAIL_COMPOSE_REMOVE") == "1":
        sys.exit(1)
    if command == "rm":
        for service in selected:
            state(service).unlink(missing_ok=True)
    elif command == "ps":
        if os.environ.get("FAIL_COMPOSE_REMOVE") == "1":
            sys.exit(1)
        for service in selected:
            if state(service).exists():
                print(ids[service])
    elif command == "pull" and os.environ.get("FAIL_STAGE") == "pull":
        sys.exit(71)
    elif command == "up":
        if "auth-secret-init" in args and os.environ.get("FAIL_STAGE") == "auth-secret-init":
            sys.exit(72)
        for service in selected:
            state(service).write_text(json.dumps({"image": selected_image(service)}))
        if selected and os.environ.get("FAIL_STAGE") == "recreation":
            sys.exit(73)
    elif command not in ("stop", "logs", "pull"):
        sys.exit(90)
else:
    sys.exit(91)
'''


class CanonicalRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "containers").mkdir()
        docker = self.root / "docker"
        docker.write_text(DOCKER_DOUBLE)
        docker.chmod(0o755)

    def reset_fixture(self) -> None:
        for name in ("commands", "maintenance", "effects", "journal", "label-inspections"):
            (self.root / name).unlink(missing_ok=True)
        (self.root / "ps-count").write_text("0")
        (self.root / "durable-inbox-evidence").write_text("preserved")
        for service in SERVICES:
            image = TARGET_API if service in ("api", "worker", "scheduler") else (
                TARGET_HERMES if service == "hermes" else OTHER_IMAGE
            )
            (self.root / "containers" / service).write_text(json.dumps({"image": image}))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_rollback(self, *, prior_contract: str = "v1", target_contract: str = "v1",
                     prior_hermes_contract: str | None = None,
                     target_hermes_contract: str | None = None,
                     role_verified: bool = True,
                     compatible_record: bool = True, stage: str = "", **overrides: str
                     ) -> subprocess.CompletedProcess[str]:
        self.reset_fixture()
        environment = dict(os.environ, FIXTURE=str(self.root), SERVICES=json.dumps(SERVICES),
                           OTHER_IMAGE=OTHER_IMAGE, FAIL_STAGE=stage)
        environment["PATH"] = str(self.root) + os.pathsep + os.environ["PATH"]
        environment.update(overrides)
        environment["LABELS"] = json.dumps({
            PREVIOUS_API: prior_contract,
            PREVIOUS_HERMES: prior_contract if prior_hermes_contract is None else prior_hermes_contract,
            TARGET_API: target_contract,
            TARGET_HERMES: target_contract if target_hermes_contract is None else target_hermes_contract,
        })
        variables = {
            "previous_api_image": PREVIOUS_API, "previous_worker_image": PREVIOUS_API,
            "previous_scheduler_image": PREVIOUS_API, "previous_hermes_image": PREVIOUS_HERMES,
            "target_api_image": TARGET_API, "target_hermes_image": TARGET_HERMES,
            "API_IMAGE": TARGET_API, "HERMES_IMAGE": TARGET_HERMES,
            "previous_revision": "1" * 40, "previous_image_tag": "sha-" + "1" * 40,
            "target_revision": "2" * 40, "deploy_commit": "2" * 40,
            "target_infra_image_tag": "sha-" + "2" * 40,
            "previous_web_image": OTHER_IMAGE, "rollback_admin_web_image": OTHER_IMAGE,
            "rollback_research_web_image": OTHER_IMAGE, "target_caddy_image": OTHER_IMAGE,
            "target_postgres_image": OTHER_IMAGE, "target_backup_image": OTHER_IMAGE,
            "previous_auth_login_mode": "disabled", "previous_whatsapp_backend": "disabled",
            "previous_temporary_web_pin_verifier_file": "",
            "previous_temporary_web_pin_verifier_file_host": "",
            "previous_temporary_web_pin_expires_at": "", "promotion_state_file": "journal",
        }
        definitions = "\n".join(function(name) for name in (
            "canonical_image_supports_contract", "previous_images_support_canonical_contract",
            "verify_cached_rollback_images",
            "canonical_execution_container_ids", "contain_canonical_execution_surface",
            "running_image_ref", "rollback",
        ))
        contract_preflight = SOURCE.split('canonical_image_supports_contract "$API_IMAGE" api\n', 1)[1]
        contract_preflight = 'canonical_image_supports_contract "$API_IMAGE" api\n' + contract_preflight.split("auth_validator=", 1)[0]
        script = "\n".join((
            "set -Eeuo pipefail",
            "source " + shlex.quote(str(ROOT / "scripts" / "rollback_containment.sh")),
            definitions,
            "\n".join("export " + key + "=" + shlex.quote(value) for key, value in variables.items()),
            'compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)',
            EXECUTION_SERVICES.group(),
            'previous_writer_containers=()\nprevious_boundary_valid=1',
            f'automatic_rollback_available={int(compatible_record)}',
            'previous_canonical_contract_compatible=0',
            f'existing_app_role_verified={int(role_verified)}',
            'previous_worker_running=1\nprevious_scheduler_running=1\nprevious_hermes_running=1',
            'deployment_started=1\nwriter_stop_attempted=1',
            'mark_maintenance_required() { printf "%s\\n" "$1" >> maintenance; }',
            'write_promotion_state() { printf "%s\\n" "$2" >> journal; }',
            'rewrite_release_boundary() { printf "boundary\\n" >> effects; }',
            'persist_release_metadata() { printf "metadata\\n" >> effects; [[ "${FAIL_STAGE:-}" != metadata ]]; }',
            'run_production_smoke() { [[ "${FAIL_STAGE:-}" != smoke ]]; }',
            contract_preflight,
            'set +e\n(exit 42)\nrollback',
        ))
        return subprocess.run(["bash", "-c", script], cwd=self.root, env=environment,
                              text=True, capture_output=True, check=False)

    def assert_contained(self) -> None:
        self.assertEqual(sorted(p.name for p in (self.root / "containers").iterdir()),
                         ["postgres", "redis"])
        self.assertEqual((self.root / "durable-inbox-evidence").read_text(), "preserved")
        commands = [json.loads(line) for line in (self.root / "commands").read_text().splitlines()]
        self.assertFalse(any(command[0] == "start" for command in commands))
        self.assertFalse(any("up" in command for command in commands))
        self.assertFalse(any("--volumes" in command or "down" in command for command in commands))

    def test_unverified_prior_images_are_never_restarted_after_cutover(self) -> None:
        for contract in ("", "<no value>", "v0", "v2", "inspect-error"):
            with self.subTest(contract=contract):
                result = self.run_rollback(prior_contract=contract)
                self.assertEqual(result.returncode, 42, result.stderr)
                self.assert_contained()
                self.assertIn("canonical_contract_rollback_unverified",
                              (self.root / "maintenance").read_text())
                self.assertFalse((self.root / "effects").exists())

    def test_unrecorded_prior_release_is_contained_even_with_valid_labels(self) -> None:
        result = self.run_rollback(compatible_record=False)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_contained()

    def test_failed_database_role_check_cannot_restart_even_compatible_prior_code(self) -> None:
        result = self.run_rollback(role_verified=False)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_contained()
        self.assertIn("existing_app_role_compatibility_failed", (self.root / "maintenance").read_text())
        self.assertFalse((self.root / "effects").exists())

    def test_previous_api_contract_does_not_substitute_for_hermes_contract(self) -> None:
        result = self.run_rollback(prior_hermes_contract="")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_contained()
        self.assertIn("previous-hermes image", result.stderr)

    def test_compose_failure_still_removes_all_writers_by_docker_labels(self) -> None:
        result = self.run_rollback(prior_contract="v0", FAIL_COMPOSE_REMOVE="1")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_contained()

    def test_failed_authoritative_proof_keeps_maintenance_interlock(self) -> None:
        # Nine services are queried before and after removal; fail the first
        # independent final proof, even though the processes were removed.
        result = self.run_rollback(FAIL_PS_CALL="19")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_contained()
        self.assertIn("canonical_execution_containment_failed",
                      (self.root / "maintenance").read_text())

    def test_unremovable_target_is_interlocked_without_starting_prior_code(self) -> None:
        result = self.run_rollback(FAIL_COMPOSE_REMOVE="1", FAIL_DOCKER_RM="1")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn("canonical_execution_containment_failed",
                      (self.root / "maintenance").read_text())
        self.assertFalse((self.root / "effects").exists())
        self.assertFalse((self.root / "journal").exists())
        commands = [json.loads(line) for line in (self.root / "commands").read_text().splitlines()]
        self.assertFalse(any(command[0] == "start" or "up" in command for command in commands))

    def test_compatibility_is_rechecked_before_restoring_prior_pointers(self) -> None:
        result = self.run_rollback(FAIL_CONTRACT_RECHECK=PREVIOUS_API)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_contained()
        self.assertFalse((self.root / "effects").exists())
        self.assertTrue((self.root / "maintenance").exists())

    def test_compatible_previous_release_can_resume_and_persist_hybrid_boundary(self) -> None:
        result = self.run_rollback()
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn("Application rollback succeeded", result.stderr)
        self.assertEqual((self.root / "journal").read_text(), "HYBRID_PERSISTED\n")
        self.assertFalse((self.root / "maintenance").exists())
        for service in ("api", "worker", "scheduler", "hermes"):
            expected = PREVIOUS_HERMES if service == "hermes" else PREVIOUS_API
            self.assertEqual(json.loads((self.root / "containers" / service).read_text())["image"], expected)
        self.assertEqual((self.root / "durable-inbox-evidence").read_text(), "preserved")

    def test_failed_compatible_rollback_also_contains_partial_workers_and_egress(self) -> None:
        for stage in ("cache", "auth-secret-init", "recreation", "smoke", "metadata"):
            with self.subTest(stage=stage):
                result = self.run_rollback(stage=stage)
                self.assertEqual(result.returncode, 42, result.stderr)
                self.assertEqual(sorted(p.name for p in (self.root / "containers").iterdir()),
                                 ["postgres", "redis"])
                self.assertTrue((self.root / "maintenance").exists())
                self.assertFalse((self.root / "journal").exists())

    def test_target_contract_is_verified_before_stop_and_migration(self) -> None:
        result = self.run_rollback(target_contract="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.root / "containers").iterdir())), len(SERVICES))
        self.assertFalse((self.root / "maintenance").exists())
        preflight = SOURCE.index('canonical_image_supports_contract "$API_IMAGE" api\n')
        self.assertLess(preflight, SOURCE.index("writer_stop_attempted=1"))
        self.assertLess(SOURCE.index("previous_canonical_contract_compatible=1"),
                        SOURCE.index("writer_stop_attempted=1"))
        self.assertLess(SOURCE.index("deployment_started=1"),
                        SOURCE.index('"${compose[@]}" --profile tools run --rm migrate'))
        execution_services = set(shlex.split(EXECUTION_SERVICES.group().split("=(", 1)[1][:-1]))
        self.assertEqual(execution_services, set(SERVICES) - {"postgres", "redis"})

    def test_mutable_image_cannot_claim_contract(self) -> None:
        script = function("canonical_image_supports_contract") + '\ncanonical_image_supports_contract example/api:latest api\n'
        result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("immutable image digest", result.stderr)

    def test_target_hermes_contract_is_also_required_before_migration(self) -> None:
        result = self.run_rollback(target_hermes_contract="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.root / "containers").iterdir())), len(SERVICES))
        self.assertFalse((self.root / "maintenance").exists())
        self.assertIn("hermes image", result.stderr)

    def test_migration_guard_requires_hermes_stopped_and_successful_inventory(self) -> None:
        for state, inventory, expected in (("exited", "ok", 0), ("running", "ok", 1),
                                           ("exited", "failed", 1)):
            with self.subTest(state=state, inventory=inventory):
                script = "\n".join((
                    "set -Eeuo pipefail", function("assert_application_writers_stopped"),
                    "application_writer_services=(api worker scheduler voice)",
                    "application_writer_services+=(hermes)", "compose=(fake_compose)",
                    'fake_compose() { [[ "$INVENTORY" == ok ]] || return 1; '
                    '[[ "${!#}" != hermes ]] || printf "%064d\\n" 1; }',
                    'docker() { printf "%s\\n" "$STATE"; }',
                    "assert_application_writers_stopped",
                ))
                result = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                                        env=dict(os.environ, STATE=state, INVENTORY=inventory),
                                        check=False)
                self.assertEqual(result.returncode, expected, result.stderr)
        self.assertIn("application_writer_services+=(hermes)", SOURCE)


if __name__ == "__main__":
    unittest.main()
