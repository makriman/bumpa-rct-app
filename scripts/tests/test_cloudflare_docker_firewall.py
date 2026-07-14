from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "cloudflare_docker_firewall.py"
SPEC = importlib.util.spec_from_file_location("cloudflare_docker_firewall", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
firewall = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = firewall
SPEC.loader.exec_module(firewall)


def state() -> firewall.FirewallState:
    return firewall.FirewallState(
        "eth0",
        "eth0",
        ("103.21.244.0/22", "173.245.48.0/20"),
        ("2606:4700::/32", "2a06:98c0::/29"),
        {"ipv4_sha256": "a" * 64, "ipv6_sha256": "b" * 64},
    )


class RangeAndStateTests(unittest.TestCase):
    def test_state_round_trip_is_strict_and_canonical(self) -> None:
        expected = state()
        self.assertEqual(firewall.parse_state(expected.to_json()), expected)

    def test_state_rejects_empty_malformed_unsorted_or_optional_ipv6(self) -> None:
        valid = json.loads(state().to_json())
        mutations = []
        empty = dict(valid)
        empty["ipv4_ranges"] = []
        mutations.append(empty)
        unsorted = dict(valid)
        unsorted["ipv4_ranges"] = list(reversed(valid["ipv4_ranges"]))
        mutations.append(unsorted)
        no_ipv6_interface = dict(valid)
        no_ipv6_interface["ipv6_interface"] = None
        mutations.append(no_ipv6_interface)
        extra = dict(valid)
        extra["unexpected"] = True
        mutations.append(extra)
        bad_hash = dict(valid)
        bad_hash["source_hashes"] = {
            "ipv4_sha256": "not-a-hash",
            "ipv6_sha256": "b" * 64,
        }
        mutations.append(bad_hash)
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaises(firewall.DockerFirewallError):
                    firewall.parse_state(json.dumps(payload))

    def test_range_validator_rejects_private_wrong_family_duplicate_and_overlap(
        self,
    ) -> None:
        for payload, family in [
            (b"\n", 4),
            (b"10.0.0.0/8\n", 4),
            (b"2606:4700::/32\n", 4),
            (b"173.245.48.0/20\n173.245.48.0/20\n", 4),
            (b"173.245.48.0/20\n173.245.48.0/21\n", 4),
        ]:
            with self.subTest(payload=payload, family=family):
                with self.assertRaises(firewall.DockerFirewallError):
                    firewall.validate_ranges(payload, family)


class RulesetTests(unittest.TestCase):
    def test_ruleset_is_cloudflare_only_for_web_and_returns_non_web(self) -> None:
        ranges = ("103.21.244.0/22", "173.245.48.0/20")
        rules = firewall.build_chain_rules(ranges)
        self.assertEqual(rules[-1], ("-j", "RETURN"))
        for port in firewall.PORTS:
            allows = [
                rule
                for rule in rules
                if "--ctorigdstport" in rule
                and rule[rule.index("--ctorigdstport") + 1] == str(port)
                and rule[-1] == "RETURN"
            ]
            drops = [
                rule
                for rule in rules
                if "--ctorigdstport" in rule
                and rule[rule.index("--ctorigdstport") + 1] == str(port)
                and rule[-1] == "DROP"
            ]
            self.assertEqual(len(allows), len(ranges))
            self.assertEqual(len(drops), 1)
            self.assertTrue(
                all("--ctdir" in rule and "ORIGINAL" in rule for rule in allows)
            )
        self.assertFalse(any("22" in rule for rule in rules))

    def test_ordered_verification_rejects_early_non_web_return(self) -> None:
        expected = firewall.build_chain_rules(("173.245.48.0/20",))
        valid_output = "\n".join(
            f"-A {firewall.CHAIN} {' '.join(rule)}" for rule in expected
        )
        self.assertEqual(
            firewall.canonical_chain_output(valid_output),
            tuple(firewall.canonical_chain_rule(rule) for rule in expected),
        )
        reordered = (expected[-1], *expected[:-1])
        invalid_output = "\n".join(
            f"-A {firewall.CHAIN} {' '.join(rule)}" for rule in reordered
        )
        self.assertNotEqual(
            firewall.canonical_chain_output(invalid_output),
            tuple(firewall.canonical_chain_rule(rule) for rule in expected),
        )

    def test_restore_payload_touches_only_the_managed_chain(self) -> None:
        payload = firewall.build_restore_payload(("173.245.48.0/20",))
        self.assertIn(f"-F {firewall.CHAIN}\n", payload)
        self.assertNotIn(f"-F {firewall.DOCKER_CHAIN}", payload)
        self.assertNotIn("-P FORWARD", payload)
        self.assertNotIn("COMMIT\n*", payload)
        self.assertTrue(payload.endswith("COMMIT\n"))

    def test_hook_parser_counts_only_managed_chain_jumps(self) -> None:
        output = """-N DOCKER-USER
-A DOCKER-USER -i eth0 -j BUMPABESTIE_CF_P
-A DOCKER-USER -p tcp --dport 9999 -j ACCEPT
-A DOCKER-USER -i old0 -j BUMPABESTIE_CF_P
-A DOCKER-USER -j RETURN
"""
        self.assertEqual(firewall.managed_hook_positions(output), (1, 3))

    def test_pregate_rules_are_cloudflare_only_before_dnat(self) -> None:
        ranges = ("103.21.244.0/22", "173.245.48.0/20")
        rules = firewall.build_pregate_chain_rules(ranges)
        self.assertEqual(rules[-1], ("-j", "RETURN"))
        for port in firewall.PORTS:
            allows = [
                rule
                for rule in rules
                if "--dport" in rule
                and rule[rule.index("--dport") + 1] == str(port)
                and rule[-1] == "RETURN"
            ]
            drops = [
                rule
                for rule in rules
                if "--dport" in rule
                and rule[rule.index("--dport") + 1] == str(port)
                and rule[-1] == "DROP"
            ]
            self.assertEqual(len(allows), len(ranges))
            self.assertEqual(len(drops), 1)
        self.assertFalse(any("22" in rule for rule in rules))
        payload = firewall.build_pregate_restore_payload(ranges)
        self.assertTrue(payload.startswith("*raw\n"))
        self.assertIn(f"-F {firewall.PREGATE_CHAIN}\n", payload)
        self.assertNotIn("-F PREROUTING", payload)
        self.assertNotIn("-P PREROUTING", payload)

    def test_pregate_ordered_verification_rejects_early_return(self) -> None:
        expected = firewall.build_pregate_chain_rules(("173.245.48.0/20",))
        valid_output = "\n".join(
            f"-A {firewall.PREGATE_CHAIN} {' '.join(rule)}" for rule in expected
        )
        expected_order = tuple(
            firewall.canonical_pregate_chain_rule(rule) for rule in expected
        )
        self.assertEqual(
            firewall.canonical_pregate_chain_output(valid_output), expected_order
        )
        reordered = (expected[-1], *expected[:-1])
        invalid_output = "\n".join(
            f"-A {firewall.PREGATE_CHAIN} {' '.join(rule)}" for rule in reordered
        )
        self.assertNotEqual(
            firewall.canonical_pregate_chain_output(invalid_output), expected_order
        )

    def test_pregate_hook_parser_counts_only_managed_chain_jumps(self) -> None:
        output = """-P PREROUTING ACCEPT
-A PREROUTING -i eth0 -j BUMPABESTIE_CF_PRE
-A PREROUTING -p tcp --dport 9999 -j ACCEPT
-A PREROUTING -i old0 -j BUMPABESTIE_CF_PRE
"""
        self.assertEqual(firewall.pregate_hook_positions(output), (1, 3))


class HookBackend(firewall.NetfilterBackend):
    def __init__(self) -> None:
        self.family = 4
        self.rules = [
            "-A DOCKER-USER -p tcp --dport 9999 -j ACCEPT",
            "-A DOCKER-USER -i old0 -j BUMPABESTIE_CF_P",
            "-A DOCKER-USER -j RETURN",
        ]
        self.events: list[tuple[str, ...]] = []

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del input_text, check
        self.events.append(arguments)
        if "-I" in arguments:
            index = arguments.index("-I")
            interface = arguments[arguments.index("-i") + 1]
            target = arguments[arguments.index("-j") + 1]
            self.rules.insert(
                int(arguments[index + 2]) - 1,
                f"-A DOCKER-USER -i {interface} -j {target}",
            )
        elif "-D" in arguments:
            index = arguments.index("-D")
            del self.rules[int(arguments[index + 2]) - 1]
        output = "-N DOCKER-USER\n" + "\n".join(self.rules) + "\n"
        return subprocess.CompletedProcess([], 0, output, "")


class HookAndRollbackTests(unittest.TestCase):
    def test_compliant_backend_apply_is_mutation_free(self) -> None:
        class CompliantBackend(firewall.NetfilterBackend):
            def __init__(self) -> None:
                self.verify_calls = 0

            def verify(self, interface: str, ranges: tuple[str, ...]) -> None:
                self.verify_calls += 1

            def replace_chain(self, ranges: tuple[str, ...]) -> None:
                raise AssertionError("compliant apply must not replace the chain")

            def ensure_first_hook(self, interface: str) -> None:
                raise AssertionError("compliant apply must not insert a hook")

        backend = CompliantBackend()
        backend.apply("eth0", ("173.245.48.0/20",))
        self.assertEqual(backend.verify_calls, 1)

    def test_hook_is_inserted_first_and_old_duplicates_are_removed(self) -> None:
        backend = HookBackend()
        backend.ensure_first_hook("eth0")
        self.assertEqual(backend.rules[0], "-A DOCKER-USER -i eth0 -j BUMPABESTIE_CF_P")
        self.assertEqual(
            sum(rule.endswith("-j BUMPABESTIE_CF_P") for rule in backend.rules), 1
        )
        self.assertIn("-A DOCKER-USER -p tcp --dport 9999 -j ACCEPT", backend.rules)
        self.assertIn("-A DOCKER-USER -j RETURN", backend.rules)

    def test_refresh_restores_prior_state_after_candidate_failure(self) -> None:
        candidate = state()
        prior = dataclasses_replace(candidate, ipv4_ranges=("173.245.48.0/20",))
        writes: list[firewall.FirewallState] = []
        applications: list[firewall.FirewallState] = []

        def apply(value: firewall.FirewallState) -> None:
            applications.append(value)
            if value == candidate:
                raise firewall.DockerFirewallError("injected failure")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "state.json"
            state_path.write_text("placeholder", encoding="utf-8")
            with (
                mock.patch.object(firewall, "STATE_PATH", state_path),
                mock.patch.object(
                    firewall, "create_backup", return_value=Path("/backup")
                ),
                mock.patch.object(firewall, "load_state", return_value=prior),
                mock.patch.object(firewall, "verify_state"),
                mock.patch.object(
                    firewall, "atomic_write_state", side_effect=writes.append
                ),
                mock.patch.object(firewall, "apply_state", side_effect=apply),
                mock.patch.object(firewall, "apply_fail_closed") as fail_closed,
            ):
                with self.assertRaises(firewall.DockerFirewallError):
                    firewall.refresh(candidate)
        self.assertEqual(writes, [prior])
        self.assertEqual(applications, [candidate, prior])
        fail_closed.assert_not_called()

    def test_first_refresh_failure_installs_emergency_web_deny(self) -> None:
        candidate = state()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "state.json"
            with (
                mock.patch.object(firewall, "STATE_PATH", state_path),
                mock.patch.object(
                    firewall, "create_backup", return_value=Path("/backup")
                ),
                mock.patch.object(
                    firewall, "managed_state_present", return_value=False
                ),
                mock.patch.object(firewall, "atomic_write_state"),
                mock.patch.object(
                    firewall,
                    "apply_state",
                    side_effect=firewall.DockerFirewallError("injected failure"),
                ),
                mock.patch.object(firewall, "apply_fail_closed") as fail_closed,
            ):
                with self.assertRaisesRegex(
                    firewall.DockerFirewallError, "emergency web deny is active"
                ):
                    firewall.refresh(candidate)
        fail_closed.assert_called_once_with()

    def test_refresh_rejects_persisted_state_that_does_not_match_live_rules(
        self,
    ) -> None:
        candidate = state()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "state.json"
            state_path.write_text("placeholder", encoding="utf-8")
            with (
                mock.patch.object(firewall, "STATE_PATH", state_path),
                mock.patch.object(firewall, "load_state", return_value=candidate),
                mock.patch.object(
                    firewall,
                    "verify_state",
                    side_effect=firewall.DockerFirewallError("live mismatch"),
                ),
                mock.patch.object(firewall, "create_backup") as create_backup,
                mock.patch.object(firewall, "apply_state") as apply_state,
            ):
                with self.assertRaisesRegex(
                    firewall.DockerFirewallError, "live mismatch"
                ):
                    firewall.refresh(candidate)
        create_backup.assert_not_called()
        apply_state.assert_not_called()

    def test_combined_apply_installs_pregate_before_docker_gate(self) -> None:
        events: list[tuple[str, int]] = []

        class Backend:
            def __init__(self, family: int) -> None:
                self.family = family

            def apply(self, interface: str, ranges: tuple[str, ...]) -> None:
                del interface, ranges
                events.append((type(self).__name__, self.family))

        class Pregate(Backend):
            pass

        class DockerGate(Backend):
            pass

        with (
            mock.patch.object(firewall, "require_external_interfaces"),
            mock.patch.object(firewall, "PreDnatBackend", Pregate),
            mock.patch.object(firewall, "NetfilterBackend", DockerGate),
        ):
            firewall.apply_state(state())
        self.assertEqual(
            events,
            [("Pregate", 4), ("Pregate", 6), ("DockerGate", 4), ("DockerGate", 6)],
        )

    def test_pregate_fail_closed_uses_empty_allowlists_for_both_families(
        self,
    ) -> None:
        events: list[tuple[int, str, tuple[str, ...]]] = []

        class Pregate:
            def __init__(self, family: int) -> None:
                self.family = family

            def apply(self, interface: str, ranges: tuple[str, ...]) -> None:
                events.append((self.family, interface, ranges))

        with (
            mock.patch.object(
                firewall, "expected_external_interfaces", return_value=("eth0", "eth0")
            ),
            mock.patch.object(firewall, "PreDnatBackend", Pregate),
        ):
            firewall.apply_pregate_fail_closed()
        self.assertEqual(events, [(4, "eth0", ()), (6, "eth0", ())])


def dataclasses_replace(
    value: firewall.FirewallState, **changes: object
) -> firewall.FirewallState:
    payload = {
        "ipv4_interface": value.ipv4_interface,
        "ipv6_interface": value.ipv6_interface,
        "ipv4_ranges": value.ipv4_ranges,
        "ipv6_ranges": value.ipv6_ranges,
        "source_hashes": value.source_hashes,
        **changes,
    }
    return firewall.FirewallState(**payload)


if __name__ == "__main__":
    unittest.main()
