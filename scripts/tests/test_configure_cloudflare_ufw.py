from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "configure_cloudflare_ufw.py"
SPEC = importlib.util.spec_from_file_location("configure_cloudflare_ufw", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
firewall = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = firewall
SPEC.loader.exec_module(firewall)


class FakeUfw:
    def __init__(self, rules: list[tuple[str, str, str]]) -> None:
        self.rules = list(rules)
        self.events: list[tuple[object, ...]] = []
        self.fail_delete = False

    def _numbered(self) -> tuple[object, ...]:
        return tuple(
            firewall.Rule(index, target, source, comment)
            for index, (target, source, comment) in enumerate(self.rules, start=1)
        )

    def numbered_status(self) -> str:
        lines = [
            "Status: active",
            "",
            "     To                         Action      From",
        ]
        for rule in self._numbered():
            comment = f" # {rule.comment}" if rule.comment else ""
            lines.append(
                f"[{rule.number:2d}] {rule.target:<26} ALLOW IN    {rule.source}{comment}"
            )
        return "\n".join(lines) + "\n"

    def verbose_status(self) -> str:
        return "Status: active\nDefault: deny (incoming), allow (outgoing)\n"

    def require_ipv6(self) -> None:
        self.events.append(("ipv6-preflight",))

    def preflight_allow(self, source: str, port: int, comment: str) -> None:
        self.events.append(("preflight", port, source, comment))

    def allow(self, source: str, port: int, comment: str) -> None:
        self.events.append(("allow", port, source, comment))
        key = (f"{port}/tcp", source, comment)
        if key not in self.rules:
            self.rules.append(key)

    def delete_number(self, number: int) -> None:
        self.events.append(("delete", number))
        del self.rules[number - 1]
        if self.fail_delete:
            raise firewall.FirewallError("injected deletion failure")

    def reload(self) -> None:
        self.events.append(("reload",))


class FakeBackups:
    def __init__(self, ufw: FakeUfw) -> None:
        self.ufw = ufw
        self.snapshot: list[tuple[str, str, str]] | None = None
        self.created = False
        self.restored = False

    def create(self, **_: object) -> Path:
        self.created = True
        self.snapshot = list(self.ufw.rules)
        self.ufw.events.append(("backup",))
        return Path("/validated/backup")

    def restore(self, _: Path) -> None:
        assert self.snapshot is not None
        self.ufw.rules = list(self.snapshot)
        self.restored = True
        self.ufw.events.append(("rollback",))


class RangeValidationTests(unittest.TestCase):
    def test_accepts_canonical_global_ranges(self) -> None:
        self.assertEqual(
            firewall.validate_ranges(b"173.245.48.0/20\n103.21.244.0/22\n", 4),
            ("103.21.244.0/22", "173.245.48.0/20"),
        )
        self.assertEqual(
            firewall.validate_ranges(b"2606:4700::/32\n2a06:98c0::/29\n", 6),
            ("2606:4700::/32", "2a06:98c0::/29"),
        )

    def test_rejects_empty_malformed_wrong_family_or_private_ranges(self) -> None:
        for payload, family in [
            (b"\n", 4),
            (b"not-a-cidr\n", 4),
            (b"2606:4700::/32\n", 4),
            (b"10.0.0.0/8\n", 4),
            (b"0.0.0.0/0\n", 4),
        ]:
            with self.subTest(payload=payload, family=family):
                with self.assertRaises(firewall.FirewallError):
                    firewall.validate_ranges(payload, family)

    def test_rejects_noncanonical_duplicate_and_overlapping_ranges(self) -> None:
        for payload in [
            b"173.245.48.1/20\n",
            b"173.245.48.0/20\n173.245.48.0/20\n",
            b"173.245.48.0/20\n173.245.48.0/21\n",
        ]:
            with self.subTest(payload=payload):
                with self.assertRaises(firewall.FirewallError):
                    firewall.validate_ranges(payload, 4)


class RuleTests(unittest.TestCase):
    def test_parses_ipv4_ipv6_generic_and_commented_rules(self) -> None:
        output = """Status: active

[ 1] 80/tcp                    ALLOW IN    Anywhere
[ 2] 443/tcp (v6)             ALLOW IN    2606:4700::/32 (v6) # bumpabestie-cloudflare-origin
[ 3] 22/tcp                    ALLOW IN    203.0.113.8
[ 4] 443/tcp                   DENY IN     Anywhere
"""
        rules = firewall.parse_numbered_rules(output)
        self.assertEqual(
            rules,
            (
                firewall.Rule(1, "80/tcp", "Anywhere", ""),
                firewall.Rule(
                    2,
                    "443/tcp",
                    "2606:4700::/32",
                    firewall.MANAGED_COMMENT,
                ),
                firewall.Rule(3, "22/tcp", "203.0.113.8", ""),
                firewall.Rule(4, "443/tcp", "Anywhere", "", "DENY IN"),
            ),
        )
        self.assertEqual(rules[2].normalized_source, "203.0.113.8/32")

    def test_assessment_separates_broad_stale_and_unmanaged_rules(self) -> None:
        desired = firewall.desired_rule_keys(["173.245.48.0/20"])
        rules = (
            firewall.Rule(1, "80/tcp", "173.245.48.0/20", firewall.MANAGED_COMMENT),
            firewall.Rule(2, "443/tcp", "Anywhere", ""),
            firewall.Rule(3, "443/tcp", "103.21.244.0/22", firewall.MANAGED_COMMENT),
            firewall.Rule(4, "80/tcp", "198.51.100.0/24", "handmade"),
        )
        assessment = firewall.assess_rules(rules, desired)
        self.assertEqual(assessment.missing, ((443, "173.245.48.0/20"),))
        self.assertEqual(assessment.broad_rule_numbers, (2,))
        self.assertEqual(assessment.stale_managed_numbers, (3,))
        self.assertEqual(
            tuple(rule.number for rule in assessment.unexpected_web_rules), (4,)
        )

    def test_non_allow_web_rule_requires_operator_review(self) -> None:
        desired = firewall.desired_rule_keys(["173.245.48.0/20"])
        assessment = firewall.assess_rules(
            (firewall.Rule(1, "443/tcp", "Anywhere", "", "DENY IN"),), desired
        )
        self.assertEqual(
            tuple(rule.number for rule in assessment.unexpected_web_rules), (1,)
        )
        self.assertFalse(assessment.broad_rule_numbers)

    def test_multiport_or_range_web_rules_require_operator_review(self) -> None:
        desired = firewall.desired_rule_keys(["173.245.48.0/20"])
        assessment = firewall.assess_rules(
            (
                firewall.Rule(1, "80,443/tcp", "Anywhere", ""),
                firewall.Rule(2, "80:443/tcp", "Anywhere", ""),
            ),
            desired,
        )
        self.assertEqual(
            tuple(rule.number for rule in assessment.unexpected_web_rules), (1, 2)
        )
        self.assertFalse(assessment.broad_rule_numbers)

    def test_all_port_application_profile_and_unknown_allows_require_review(
        self,
    ) -> None:
        desired = firewall.desired_rule_keys(["173.245.48.0/20"])
        assessment = firewall.assess_rules(
            (
                firewall.Rule(1, "Anywhere", "Anywhere", ""),
                firewall.Rule(2, "Nginx Full", "Anywhere", ""),
                firewall.Rule(3, "8443/tcp", "Anywhere", ""),
            ),
            desired,
        )
        self.assertEqual(
            tuple(rule.number for rule in assessment.unexpected_web_rules), (1, 2, 3)
        )

    def test_broad_or_second_ssh_source_is_rejected(self) -> None:
        nominated = "8.8.8.8/32"
        rules = (
            firewall.Rule(1, "22/tcp", "8.8.8.8", ""),
            firewall.Rule(2, "22/tcp", "Anywhere", ""),
            firewall.Rule(3, "22/tcp", "1.1.1.1", ""),
        )
        self.assertTrue(firewall.ssh_rule_present(rules, nominated))
        self.assertEqual(
            tuple(
                rule.number for rule in firewall.unexpected_ssh_rules(rules, nominated)
            ),
            (2, 3),
        )

    def test_limit_rules_are_treated_as_inbound_authorization(self) -> None:
        nominated = "8.8.8.8/32"
        output = """Status: active

[ 1] 22/tcp                    LIMIT IN    Anywhere
[ 2] 22/tcp                    LIMIT IN    8.8.8.8
[ 3] 8443/tcp                  LIMIT IN    Anywhere
"""
        rules = firewall.parse_numbered_rules(output)
        self.assertEqual(tuple(rule.action for rule in rules), ("LIMIT IN",) * 3)
        self.assertEqual(
            tuple(
                rule.number
                for rule in firewall.unexpected_ssh_rules(rules, nominated)
            ),
            (1, 2),
        )
        assessment = firewall.assess_rules(rules, firewall.desired_rule_keys(()))
        self.assertEqual(
            tuple(rule.number for rule in assessment.unexpected_web_rules), (3,)
        )


class ApplyTests(unittest.TestCase):
    ranges = ("173.245.48.0/20", "2606:4700::/32")
    ssh_cidr = "8.8.8.8/32"

    def apply(self, ufw: FakeUfw, backups: FakeBackups, *, plan_only: bool = False):
        return firewall.apply_hardening(
            ufw,
            backups,
            ranges=self.ranges,
            source_hashes={"ipv4_sha256": "a" * 64, "ipv6_sha256": "b" * 64},
            ssh_cidr=self.ssh_cidr,
            plan_only=plan_only,
        )

    def test_adds_and_verifies_ssh_and_cloudflare_before_deleting_anywhere(
        self,
    ) -> None:
        ufw = FakeUfw([("80/tcp", "Anywhere", ""), ("443/tcp", "Anywhere", "")])
        backups = FakeBackups(ufw)
        message, backup = self.apply(ufw, backups)
        self.assertIn("restricted", message)
        self.assertEqual(backup, Path("/validated/backup"))
        self.assertTrue(backups.created)
        allow_events = [
            index for index, event in enumerate(ufw.events) if event[0] == "allow"
        ]
        delete_events = [
            index for index, event in enumerate(ufw.events) if event[0] == "delete"
        ]
        self.assertTrue(allow_events and delete_events)
        self.assertLess(max(allow_events), min(delete_events))
        self.assertEqual(ufw.events[allow_events[0]][1:3], (22, self.ssh_cidr))
        final_rules = firewall.parse_numbered_rules(ufw.numbered_status())
        firewall._verify_compliant(
            ufw, firewall.desired_rule_keys(self.ranges), self.ssh_cidr
        )
        self.assertTrue(firewall.ssh_rule_present(final_rules, self.ssh_cidr))

    def test_failure_after_mutation_restores_exact_prior_rules(self) -> None:
        prior = [
            ("22/tcp", self.ssh_cidr, ""),
            ("80/tcp", "Anywhere", ""),
            ("443/tcp", "Anywhere", ""),
        ]
        ufw = FakeUfw(prior)
        ufw.fail_delete = True
        backups = FakeBackups(ufw)
        with self.assertRaises(firewall.FirewallError):
            self.apply(ufw, backups)
        self.assertTrue(backups.restored)
        self.assertEqual(ufw.rules, prior)

    def test_keyboard_interrupt_after_mutation_also_restores_prior_rules(self) -> None:
        prior = [
            ("22/tcp", self.ssh_cidr, ""),
            ("80/tcp", "Anywhere", ""),
            ("443/tcp", "Anywhere", ""),
        ]

        class InterruptedUfw(FakeUfw):
            def delete_number(self, number: int) -> None:
                super().delete_number(number)
                raise KeyboardInterrupt

        ufw = InterruptedUfw(prior)
        backups = FakeBackups(ufw)
        with self.assertRaises(firewall.FirewallError):
            self.apply(ufw, backups)
        self.assertTrue(backups.restored)
        self.assertEqual(ufw.rules, prior)

    def test_compliant_rerun_is_idempotent_and_creates_no_backup(self) -> None:
        rules = [("22/tcp", self.ssh_cidr, "")]
        rules.extend(
            (f"{port}/tcp", source, firewall.MANAGED_COMMENT)
            for source in self.ranges
            for port in firewall.WEB_PORTS
        )
        ufw = FakeUfw(rules)
        backups = FakeBackups(ufw)
        message, backup = self.apply(ufw, backups)
        self.assertIn("already compliant", message)
        self.assertIsNone(backup)
        self.assertFalse(backups.created)
        self.assertFalse(any(event[0] in {"allow", "delete"} for event in ufw.events))

    def test_plan_preflights_but_does_not_mutate(self) -> None:
        prior = [("80/tcp", "Anywhere", ""), ("443/tcp", "Anywhere", "")]
        ufw = FakeUfw(prior)
        backups = FakeBackups(ufw)
        message, backup = self.apply(ufw, backups, plan_only=True)
        self.assertIn("ssh_add=1", message)
        self.assertIn("cloudflare_add=4", message)
        self.assertIn("web_delete=2", message)
        self.assertIsNone(backup)
        self.assertEqual(ufw.rules, prior)
        self.assertFalse(backups.created)

    def test_unmanaged_web_rule_fails_before_backup_or_mutation(self) -> None:
        ufw = FakeUfw([("80/tcp", "8.8.4.0/24", "handmade")])
        backups = FakeBackups(ufw)
        with self.assertRaises(firewall.FirewallError):
            self.apply(ufw, backups)
        self.assertFalse(backups.created)
        self.assertFalse(
            any(event[0] in {"allow", "delete", "reload"} for event in ufw.events)
        )

    def test_broad_ssh_rule_fails_before_backup_or_mutation(self) -> None:
        ufw = FakeUfw(
            [
                ("22/tcp", self.ssh_cidr, ""),
                ("22/tcp", "Anywhere", ""),
                ("80/tcp", "Anywhere", ""),
                ("443/tcp", "Anywhere", ""),
            ]
        )
        backups = FakeBackups(ufw)
        with self.assertRaises(firewall.FirewallError):
            self.apply(ufw, backups)
        self.assertFalse(backups.created)
        self.assertFalse(
            any(event[0] in {"allow", "delete", "reload"} for event in ufw.events)
        )


if __name__ == "__main__":
    unittest.main()
