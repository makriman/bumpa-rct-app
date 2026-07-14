#!/usr/bin/env python3
"""Persist Cloudflare-only access to Docker-published web ports.

UFW's INPUT chain does not police traffic DNATed to published containers. This
tool owns a pre-DNAT raw/PREROUTING gate that can be installed before Docker
starts and a defense-in-depth filter/DOCKER-USER gate after Docker starts. Both
accept only Cloudflare sources for ports 80 and 443 and preserve non-web traffic.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence
from urllib.parse import urljoin, urlsplit


IPV4_URL = "https://www.cloudflare.com/ips-v4/"
IPV6_URL = "https://www.cloudflare.com/ips-v6/"
FETCH_HOST = "www.cloudflare.com"
STATE_PATH = Path("/etc/bumpabestie/cloudflare-docker-firewall.json")
BACKUP_ROOT = Path("/var/lib/bumpabestie/firewall-backups/docker")
LOCK_PATH = Path("/run/bumpabestie-cloudflare-docker-firewall/operation.lock")
CHAIN = "BUMPABESTIE_CF_P"
DOCKER_CHAIN = "DOCKER-USER"
PREGATE_CHAIN = "BUMPABESTIE_CF_PRE"
PREGATE_PARENT_CHAIN = "PREROUTING"
PORTS = (80, 443)
MAX_RESPONSE_BYTES = 64 * 1024
MAX_RANGES = 256
REFRESH_CONFIRMATION = "enforce-cloudflare-in-docker-user"
ROLLBACK_CONFIRMATION = "restore-previous-docker-firewall"
INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


class DockerFirewallError(RuntimeError):
    """A fail-closed validation or netfilter operation failed."""


@dataclasses.dataclass(frozen=True)
class FirewallState:
    ipv4_interface: str
    ipv6_interface: str
    ipv4_ranges: tuple[str, ...]
    ipv6_ranges: tuple[str, ...]
    source_hashes: dict[str, str]

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "schema_version": 1,
                    "ipv4_interface": self.ipv4_interface,
                    "ipv6_interface": self.ipv6_interface,
                    "ipv4_ranges": list(self.ipv4_ranges),
                    "ipv6_ranges": list(self.ipv6_ranges),
                    "ports": list(PORTS),
                    "source_hashes": self.source_hashes,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


def _termination_requested(signum: int, _frame: object) -> None:
    raise DockerFirewallError(f"firewall operation interrupted by signal {signum}")


@contextlib.contextmanager
def _defer_termination() -> Iterable[None]:
    handled = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in handled}
    try:
        for signum in handled:
            signal.signal(signum, signal.SIG_IGN)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _https_get(url: str, redirects_remaining: int = 2) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != FETCH_HOST:
        raise DockerFirewallError("range URL left the pinned Cloudflare HTTPS host")
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=15, context=context
    )
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    try:
        connection.request(
            "GET", path, headers={"User-Agent": "BumpaBestie-docker-firewall/1"}
        )
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            if redirects_remaining <= 0:
                raise DockerFirewallError("too many Cloudflare range redirects")
            location = response.getheader("Location")
            if not location:
                raise DockerFirewallError("Cloudflare redirect omitted its location")
            return _https_get(urljoin(url, location), redirects_remaining - 1)
        if response.status != 200:
            raise DockerFirewallError(
                f"Cloudflare range endpoint returned HTTP {response.status}"
            )
        if (
            not (response.getheader("Content-Type") or "")
            .lower()
            .startswith("text/plain")
        ):
            raise DockerFirewallError("Cloudflare range endpoint was not text/plain")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise DockerFirewallError("Cloudflare range response was too large")
        return payload
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise DockerFirewallError(
            "unable to retrieve Cloudflare ranges over verified TLS"
        ) from exc
    finally:
        connection.close()


def validate_ranges(payload: bytes, family: int) -> tuple[str, ...]:
    try:
        lines = [
            line.strip()
            for line in payload.decode("ascii").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError as exc:
        raise DockerFirewallError("Cloudflare ranges were not ASCII") from exc
    if not lines or len(lines) > MAX_RANGES:
        raise DockerFirewallError(f"Cloudflare IPv{family} range count was invalid")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    seen: set[str] = set()
    for line in lines:
        if re.search(r"\s", line):
            raise DockerFirewallError("Cloudflare range contained whitespace")
        try:
            network = ipaddress.ip_network(line, strict=True)
        except ValueError as exc:
            raise DockerFirewallError(
                "Cloudflare range contained malformed CIDR"
            ) from exc
        canonical = network.with_prefixlen
        if (
            network.version != family
            or canonical.lower() != line.lower()
            or not network.is_global
            or network.prefixlen == 0
        ):
            raise DockerFirewallError("Cloudflare range failed canonical safety checks")
        if canonical in seen:
            raise DockerFirewallError("Cloudflare range list contained a duplicate")
        seen.add(canonical)
        networks.append(network)
    for index, network in enumerate(networks):
        if any(network.overlaps(other) for other in networks[index + 1 :]):
            raise DockerFirewallError("Cloudflare range list contained overlap")
    return tuple(
        network.with_prefixlen
        for network in sorted(
            networks, key=lambda item: (int(item.network_address), item.prefixlen)
        )
    )


def fetch_ranges() -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    ipv4_payload = _https_get(IPV4_URL)
    ipv6_payload = _https_get(IPV6_URL)
    return (
        validate_ranges(ipv4_payload, 4),
        validate_ranges(ipv6_payload, 6),
        {
            "ipv4_sha256": hashlib.sha256(ipv4_payload).hexdigest(),
            "ipv6_sha256": hashlib.sha256(ipv6_payload).hexdigest(),
        },
    )


def validate_interface(value: str) -> str:
    if not INTERFACE_PATTERN.fullmatch(value) or value == "lo":
        raise DockerFirewallError("external interface name is invalid")
    return value


def parse_state(payload: str) -> FirewallState:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DockerFirewallError("Docker firewall state is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "ipv4_interface",
        "ipv6_interface",
        "ipv4_ranges",
        "ipv6_ranges",
        "ports",
        "source_hashes",
    }:
        raise DockerFirewallError("Docker firewall state has unexpected fields")
    if raw["schema_version"] != 1 or raw["ports"] != list(PORTS):
        raise DockerFirewallError("Docker firewall state schema is unsupported")
    if not isinstance(raw["ipv4_interface"], str):
        raise DockerFirewallError("Docker firewall IPv4 interface is invalid")
    ipv4_interface = validate_interface(raw["ipv4_interface"])
    ipv6_interface = raw["ipv6_interface"]
    if not isinstance(ipv6_interface, str):
        raise DockerFirewallError("Docker firewall IPv6 interface is invalid")
    ipv6_interface = validate_interface(ipv6_interface)
    if not isinstance(raw["ipv4_ranges"], list) or not isinstance(
        raw["ipv6_ranges"], list
    ):
        raise DockerFirewallError("Docker firewall ranges are not lists")
    if any(
        not isinstance(value, str)
        for value in (*raw["ipv4_ranges"], *raw["ipv6_ranges"])
    ):
        raise DockerFirewallError("Docker firewall ranges contain non-string values")
    try:
        ipv4_payload = ("\n".join(raw["ipv4_ranges"]) + "\n").encode("ascii")
        ipv6_payload = ("\n".join(raw["ipv6_ranges"]) + "\n").encode("ascii")
    except UnicodeEncodeError as exc:
        raise DockerFirewallError("Docker firewall ranges are not ASCII") from exc
    ipv4 = validate_ranges(ipv4_payload, 4)
    ipv6 = validate_ranges(ipv6_payload, 6)
    if tuple(raw["ipv4_ranges"]) != ipv4 or tuple(raw["ipv6_ranges"]) != ipv6:
        raise DockerFirewallError("Docker firewall ranges are not canonically sorted")
    hashes = raw["source_hashes"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != {"ipv4_sha256", "ipv6_sha256"}
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in hashes.values()
        )
    ):
        raise DockerFirewallError("Docker firewall source hashes are invalid")
    return FirewallState(ipv4_interface, ipv6_interface, ipv4, ipv6, hashes)


def build_chain_rules(ranges: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    rules: list[tuple[str, ...]] = []
    for port in PORTS:
        for source in ranges:
            rules.append(
                (
                    "-s",
                    source,
                    "-p",
                    "tcp",
                    "-m",
                    "conntrack",
                    "--ctdir",
                    "ORIGINAL",
                    "--ctorigdstport",
                    str(port),
                    "-j",
                    "RETURN",
                )
            )
        rules.append(
            (
                "-p",
                "tcp",
                "-m",
                "conntrack",
                "--ctdir",
                "ORIGINAL",
                "--ctorigdstport",
                str(port),
                "-j",
                "DROP",
            )
        )
    rules.append(("-j", "RETURN"))
    return tuple(rules)


def build_restore_payload(ranges: Sequence[str]) -> str:
    lines = ["*filter", f"-F {CHAIN}"]
    lines.extend(f"-A {CHAIN} {' '.join(rule)}" for rule in build_chain_rules(ranges))
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def build_pregate_chain_rules(ranges: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    rules: list[tuple[str, ...]] = []
    for port in PORTS:
        for source in ranges:
            rules.append(
                (
                    "-s",
                    source,
                    "-p",
                    "tcp",
                    "--dport",
                    str(port),
                    "-j",
                    "RETURN",
                )
            )
        rules.append(("-p", "tcp", "--dport", str(port), "-j", "DROP"))
    rules.append(("-j", "RETURN"))
    return tuple(rules)


def build_pregate_restore_payload(ranges: Sequence[str]) -> str:
    lines = ["*raw", f"-F {PREGATE_CHAIN}"]
    lines.extend(
        f"-A {PREGATE_CHAIN} {' '.join(rule)}"
        for rule in build_pregate_chain_rules(ranges)
    )
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def managed_hook_positions(output: str) -> tuple[int, ...]:
    positions: list[int] = []
    rule_number = 0
    for line in output.splitlines():
        if not line.startswith(f"-A {DOCKER_CHAIN} "):
            continue
        rule_number += 1
        if re.search(rf"(?:^|\s)-j {re.escape(CHAIN)}$", line):
            positions.append(rule_number)
    return tuple(positions)


def pregate_hook_positions(output: str) -> tuple[int, ...]:
    positions: list[int] = []
    rule_number = 0
    for line in output.splitlines():
        if not line.startswith(f"-A {PREGATE_PARENT_CHAIN} "):
            continue
        rule_number += 1
        if re.search(rf"(?:^|\s)-j {re.escape(PREGATE_CHAIN)}$", line):
            positions.append(rule_number)
    return tuple(positions)


def canonical_chain_rule(tokens: Sequence[str]) -> tuple[object, ...]:
    if tuple(tokens) == ("-j", "RETURN"):
        return ("non-web-return",)
    source: str | None = None
    protocol: str | None = None
    module: str | None = None
    direction: str | None = None
    original_port: int | None = None
    jump: str | None = None
    index = 0
    while index < len(tokens):
        option = tokens[index]
        if index + 1 >= len(tokens):
            raise DockerFirewallError("managed chain rule ended unexpectedly")
        value = tokens[index + 1]
        if option == "-s":
            source = ipaddress.ip_network(value, strict=True).with_prefixlen
        elif option == "-p":
            protocol = value
        elif option == "-m":
            module = value
        elif option == "--ctdir":
            direction = value
        elif option == "--ctorigdstport":
            if not value.isdigit():
                raise DockerFirewallError("managed chain port is invalid")
            original_port = int(value)
        elif option == "-j":
            jump = value
        else:
            raise DockerFirewallError("managed chain contains an unsupported option")
        index += 2
    if (
        protocol != "tcp"
        or module != "conntrack"
        or direction != "ORIGINAL"
        or original_port not in PORTS
        or jump not in {"RETURN", "DROP"}
        or (jump == "RETURN" and source is None)
        or (jump == "DROP" and source is not None)
    ):
        raise DockerFirewallError("managed chain rule semantics are invalid")
    return original_port, source, jump


def canonical_chain_output(output: str) -> tuple[tuple[object, ...], ...]:
    prefix = ("-A", CHAIN)
    result: list[tuple[object, ...]] = []
    for line in output.splitlines():
        tokens = tuple(shlex.split(line))
        if tokens[:2] == prefix:
            result.append(canonical_chain_rule(tokens[2:]))
    return tuple(result)


def canonical_pregate_chain_rule(tokens: Sequence[str]) -> tuple[object, ...]:
    if tuple(tokens) == ("-j", "RETURN"):
        return ("non-web-return",)
    source: str | None = None
    protocol: str | None = None
    module: str | None = None
    destination_port: int | None = None
    jump: str | None = None
    index = 0
    while index < len(tokens):
        option = tokens[index]
        if index + 1 >= len(tokens):
            raise DockerFirewallError("pre-DNAT chain rule ended unexpectedly")
        value = tokens[index + 1]
        if option == "-s":
            source = ipaddress.ip_network(value, strict=True).with_prefixlen
        elif option == "-p":
            protocol = value
        elif option == "-m":
            module = value
        elif option == "--dport":
            if not value.isdigit():
                raise DockerFirewallError("pre-DNAT chain port is invalid")
            destination_port = int(value)
        elif option == "-j":
            jump = value
        else:
            raise DockerFirewallError(
                "pre-DNAT chain contains an unsupported option"
            )
        index += 2
    if (
        protocol != "tcp"
        or module not in {None, "tcp"}
        or destination_port not in PORTS
        or jump not in {"RETURN", "DROP"}
        or (jump == "RETURN" and source is None)
        or (jump == "DROP" and source is not None)
    ):
        raise DockerFirewallError("pre-DNAT chain rule semantics are invalid")
    return destination_port, source, jump


def canonical_pregate_chain_output(
    output: str,
) -> tuple[tuple[object, ...], ...]:
    prefix = ("-A", PREGATE_CHAIN)
    result: list[tuple[object, ...]] = []
    for line in output.splitlines():
        tokens = tuple(shlex.split(line))
        if tokens[:2] == prefix:
            result.append(canonical_pregate_chain_rule(tokens[2:]))
    return tuple(result)


class NetfilterBackend:
    def __init__(self, family: int) -> None:
        if family not in {4, 6}:
            raise ValueError("family must be 4 or 6")
        prefix = "ip6" if family == 6 else "ip"
        self.family = family
        self.command = trusted_binary(Path(f"/usr/sbin/{prefix}tables"))
        self.restore_command = trusted_binary(Path(f"/usr/sbin/{prefix}tables-restore"))

    def run(
        self,
        arguments: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = (
            self.restore_command
            if arguments and arguments[0] == "RESTORE"
            else self.command
        )
        actual_arguments = list(
            arguments[1:] if command == self.restore_command else arguments
        )
        try:
            return subprocess.run(
                [str(command), *actual_arguments],
                input=input_text,
                text=True,
                capture_output=True,
                check=check,
                timeout=60,
                env={
                    "HOME": "/root",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                },
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerFirewallError("netfilter command failed") from exc

    def chain_exists(self, chain: str) -> bool:
        return self.run(("-w", "10", "-S", chain), check=False).returncode == 0

    def require_docker_chain(self) -> None:
        if not self.chain_exists(DOCKER_CHAIN):
            raise DockerFirewallError(
                f"IPv{self.family} DOCKER-USER chain is unavailable; Docker is not ready"
            )

    def ensure_managed_chain(self) -> None:
        if not self.chain_exists(CHAIN):
            self.run(("-w", "10", "-N", CHAIN))

    def replace_chain(self, ranges: Sequence[str]) -> None:
        self.require_docker_chain()
        created = not self.chain_exists(CHAIN)
        self.ensure_managed_chain()
        payload = build_restore_payload(ranges)
        try:
            self.run(
                ("RESTORE", "--wait", "10", "--noflush", "--test"),
                input_text=payload,
            )
            self.run(("RESTORE", "--wait", "10", "--noflush"), input_text=payload)
        except BaseException:
            if created and self.chain_exists(CHAIN):
                self.run(("-w", "10", "-F", CHAIN))
                self.run(("-w", "10", "-X", CHAIN))
            raise

    def ensure_first_hook(self, interface: str) -> None:
        self.run(("-w", "10", "-I", DOCKER_CHAIN, "1", "-i", interface, "-j", CHAIN))
        output = self.run(("-w", "10", "-S", DOCKER_CHAIN)).stdout
        positions = managed_hook_positions(output)
        if not positions or positions[0] != 1:
            raise DockerFirewallError("managed Docker firewall hook was not first")
        for position in sorted(positions[1:], reverse=True):
            self.run(("-w", "10", "-D", DOCKER_CHAIN, str(position)))

    def apply(self, interface: str, ranges: Sequence[str]) -> None:
        try:
            self.verify(interface, ranges)
            return
        except DockerFirewallError:
            pass
        self.replace_chain(ranges)
        self.ensure_first_hook(interface)
        self.verify(interface, ranges)

    def managed_present(self) -> bool:
        if self.chain_exists(CHAIN):
            return True
        if not self.chain_exists(DOCKER_CHAIN):
            return False
        output = self.run(("-w", "10", "-S", DOCKER_CHAIN)).stdout
        return bool(managed_hook_positions(output))

    def verify(self, interface: str, ranges: Sequence[str]) -> None:
        docker_rules = [
            line
            for line in self.run(("-w", "10", "-S", DOCKER_CHAIN)).stdout.splitlines()
            if line.startswith(f"-A {DOCKER_CHAIN} ")
        ]
        expected_hook = f"-A {DOCKER_CHAIN} -i {interface} -j {CHAIN}"
        if not docker_rules or docker_rules[0] != expected_hook:
            raise DockerFirewallError("managed Docker firewall hook is not first")
        if sum(line.endswith(f"-j {CHAIN}") for line in docker_rules) != 1:
            raise DockerFirewallError("managed Docker firewall hook is duplicated")
        chain_output = self.run(("-w", "10", "-S", CHAIN)).stdout
        expected = build_chain_rules(ranges)
        expected_order = tuple(canonical_chain_rule(rule) for rule in expected)
        if canonical_chain_output(chain_output) != expected_order:
            raise DockerFirewallError(
                "managed Docker firewall chain order or semantics are invalid"
            )
        for rule in expected:
            result = self.run(("-w", "10", "-C", CHAIN, *rule), check=False)
            if result.returncode != 0:
                raise DockerFirewallError(
                    "managed Docker firewall rule verification failed"
                )

    def clear(self) -> None:
        if self.chain_exists(DOCKER_CHAIN):
            output = self.run(("-w", "10", "-S", DOCKER_CHAIN)).stdout
            for position in sorted(managed_hook_positions(output), reverse=True):
                self.run(("-w", "10", "-D", DOCKER_CHAIN, str(position)))
        if self.chain_exists(CHAIN):
            self.run(("-w", "10", "-F", CHAIN))
            self.run(("-w", "10", "-X", CHAIN))


class PreDnatBackend(NetfilterBackend):
    """Own the raw/PREROUTING gate that exists independently of Docker."""

    def raw_chain_exists(self, chain: str) -> bool:
        return (
            self.run(
                ("-w", "10", "-t", "raw", "-S", chain), check=False
            ).returncode
            == 0
        )

    def require_parent_chain(self) -> None:
        if not self.raw_chain_exists(PREGATE_PARENT_CHAIN):
            raise DockerFirewallError(
                f"IPv{self.family} raw/PREROUTING is unavailable"
            )

    def ensure_managed_chain(self) -> None:
        if not self.raw_chain_exists(PREGATE_CHAIN):
            self.run(("-w", "10", "-t", "raw", "-N", PREGATE_CHAIN))

    def replace_chain(self, ranges: Sequence[str]) -> None:
        created = not self.raw_chain_exists(PREGATE_CHAIN)
        self.ensure_managed_chain()
        payload = build_pregate_restore_payload(ranges)
        try:
            self.run(
                ("RESTORE", "--wait", "10", "--noflush", "--test"),
                input_text=payload,
            )
            self.run(("RESTORE", "--wait", "10", "--noflush"), input_text=payload)
        except BaseException:
            if created and self.raw_chain_exists(PREGATE_CHAIN):
                self.run(("-w", "10", "-t", "raw", "-F", PREGATE_CHAIN))
                self.run(("-w", "10", "-t", "raw", "-X", PREGATE_CHAIN))
            raise

    def ensure_first_hook(self, interface: str) -> None:
        self.run(
            (
                "-w",
                "10",
                "-t",
                "raw",
                "-I",
                PREGATE_PARENT_CHAIN,
                "1",
                "-i",
                interface,
                "-j",
                PREGATE_CHAIN,
            )
        )
        output = self.run(
            ("-w", "10", "-t", "raw", "-S", PREGATE_PARENT_CHAIN)
        ).stdout
        positions = pregate_hook_positions(output)
        if not positions or positions[0] != 1:
            raise DockerFirewallError("managed pre-DNAT firewall hook was not first")
        for position in sorted(positions[1:], reverse=True):
            self.run(
                (
                    "-w",
                    "10",
                    "-t",
                    "raw",
                    "-D",
                    PREGATE_PARENT_CHAIN,
                    str(position),
                )
            )

    def apply(self, interface: str, ranges: Sequence[str]) -> None:
        try:
            self.verify(interface, ranges)
            return
        except DockerFirewallError:
            pass
        self.replace_chain(ranges)
        self.ensure_first_hook(interface)
        self.verify(interface, ranges)

    def managed_present(self) -> bool:
        if self.raw_chain_exists(PREGATE_CHAIN):
            return True
        output = self.run(
            ("-w", "10", "-t", "raw", "-S", PREGATE_PARENT_CHAIN),
            check=False,
        )
        return output.returncode == 0 and bool(pregate_hook_positions(output.stdout))

    def verify(self, interface: str, ranges: Sequence[str]) -> None:
        parent_rules = [
            line
            for line in self.run(
                ("-w", "10", "-t", "raw", "-S", PREGATE_PARENT_CHAIN)
            ).stdout.splitlines()
            if line.startswith(f"-A {PREGATE_PARENT_CHAIN} ")
        ]
        expected_hook = (
            f"-A {PREGATE_PARENT_CHAIN} -i {interface} -j {PREGATE_CHAIN}"
        )
        if not parent_rules or parent_rules[0] != expected_hook:
            raise DockerFirewallError("managed pre-DNAT firewall hook is not first")
        if sum(line.endswith(f"-j {PREGATE_CHAIN}") for line in parent_rules) != 1:
            raise DockerFirewallError("managed pre-DNAT firewall hook is duplicated")
        chain_output = self.run(
            ("-w", "10", "-t", "raw", "-S", PREGATE_CHAIN)
        ).stdout
        expected = build_pregate_chain_rules(ranges)
        expected_order = tuple(
            canonical_pregate_chain_rule(rule) for rule in expected
        )
        if canonical_pregate_chain_output(chain_output) != expected_order:
            raise DockerFirewallError(
                "managed pre-DNAT firewall chain order or semantics are invalid"
            )
        for rule in expected:
            result = self.run(
                ("-w", "10", "-t", "raw", "-C", PREGATE_CHAIN, *rule),
                check=False,
            )
            if result.returncode != 0:
                raise DockerFirewallError(
                    "managed pre-DNAT firewall rule verification failed"
                )


def trusted_binary(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise DockerFirewallError(f"required executable is missing: {path}") from exc
    if not resolved.is_file() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise DockerFirewallError(f"required executable is not trusted: {path}")
    return path


def detect_default_interface(family: int) -> str | None:
    ip_command = trusted_binary(Path("/usr/sbin/ip"))
    result = subprocess.run(
        [str(ip_command), f"-{family}", "route", "show", "default"],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
    )
    matches = re.findall(r"(?:^|\s)dev\s+(\S+)", result.stdout)
    if not matches:
        return None
    if len(set(matches)) != 1:
        raise DockerFirewallError(f"IPv{family} has multiple default interfaces")
    return validate_interface(matches[0])


def require_interface_exists(interface: str) -> None:
    ip_command = trusted_binary(Path("/usr/sbin/ip"))
    result = subprocess.run(
        [str(ip_command), "link", "show", "dev", interface],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
    )
    if result.returncode != 0:
        raise DockerFirewallError("configured external interface does not exist")


def expected_external_interfaces() -> tuple[str, str]:
    ipv4_interface = detect_default_interface(4)
    if ipv4_interface is None:
        raise DockerFirewallError("host has no unambiguous IPv4 default interface")
    return ipv4_interface, detect_default_interface(6) or ipv4_interface


def require_external_interfaces(ipv4_interface: str, ipv6_interface: str) -> None:
    expected_ipv4, expected_ipv6 = expected_external_interfaces()
    if (ipv4_interface, ipv6_interface) != (expected_ipv4, expected_ipv6):
        raise DockerFirewallError(
            "configured interfaces do not match the host default ingress interfaces"
        )
    require_interface_exists(ipv4_interface)
    require_interface_exists(ipv6_interface)


def _validate_private_file(path: Path) -> None:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise DockerFirewallError(
            f"required root-owned state is unavailable: {path}"
        ) from exc
    if (
        path.is_symlink()
        or not path.is_file()
        or metadata.st_uid != 0
        or metadata.st_gid != 0
    ):
        raise DockerFirewallError("Docker firewall state owner or type is unsafe")
    if metadata.st_mode & 0o777 != 0o600:
        raise DockerFirewallError("Docker firewall state must have mode 0600")


def load_state(path: Path = STATE_PATH) -> FirewallState:
    _validate_private_file(path)
    return parse_state(path.read_text(encoding="utf-8"))


def atomic_write_state(state: FirewallState, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    parent_metadata = path.parent.stat()
    if (
        path.parent.is_symlink()
        or parent_metadata.st_uid != 0
        or parent_metadata.st_gid != 0
    ):
        raise DockerFirewallError("Docker firewall state directory is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(state.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def apply_state(state: FirewallState) -> None:
    require_external_interfaces(state.ipv4_interface, state.ipv6_interface)
    PreDnatBackend(4).apply(state.ipv4_interface, state.ipv4_ranges)
    PreDnatBackend(6).apply(state.ipv6_interface, state.ipv6_ranges)
    NetfilterBackend(4).apply(state.ipv4_interface, state.ipv4_ranges)
    NetfilterBackend(6).apply(state.ipv6_interface, state.ipv6_ranges)


def verify_state(state: FirewallState) -> None:
    require_external_interfaces(state.ipv4_interface, state.ipv6_interface)
    PreDnatBackend(4).verify(state.ipv4_interface, state.ipv4_ranges)
    PreDnatBackend(6).verify(state.ipv6_interface, state.ipv6_ranges)
    NetfilterBackend(4).verify(state.ipv4_interface, state.ipv4_ranges)
    NetfilterBackend(6).verify(state.ipv6_interface, state.ipv6_ranges)


def apply_pregate_state(state: FirewallState) -> None:
    require_external_interfaces(state.ipv4_interface, state.ipv6_interface)
    PreDnatBackend(4).apply(state.ipv4_interface, state.ipv4_ranges)
    PreDnatBackend(6).apply(state.ipv6_interface, state.ipv6_ranges)


def verify_pregate_state(state: FirewallState) -> None:
    require_external_interfaces(state.ipv4_interface, state.ipv6_interface)
    PreDnatBackend(4).verify(state.ipv4_interface, state.ipv4_ranges)
    PreDnatBackend(6).verify(state.ipv6_interface, state.ipv6_ranges)


def managed_state_present() -> bool:
    return any(
        backend(family).managed_present()
        for family in (4, 6)
        for backend in (PreDnatBackend, NetfilterBackend)
    )


def apply_fail_closed() -> None:
    ipv4_interface, ipv6_interface = expected_external_interfaces()
    PreDnatBackend(4).apply(ipv4_interface, ())
    PreDnatBackend(6).apply(ipv6_interface, ())
    NetfilterBackend(4).apply(ipv4_interface, ())
    NetfilterBackend(6).apply(ipv6_interface, ())


def apply_pregate_fail_closed() -> None:
    ipv4_interface, ipv6_interface = expected_external_interfaces()
    PreDnatBackend(4).apply(ipv4_interface, ())
    PreDnatBackend(6).apply(ipv6_interface, ())


def _backup_index(path: Path) -> dict[str, str | int | bool]:
    prior = path / "prior-state.json"
    absent = path / "prior-state.absent"
    if prior.is_file() and not prior.is_symlink() and not absent.exists():
        return {
            "prior_state_present": True,
            "prior_state_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
        }
    if absent.is_file() and not absent.is_symlink() and not prior.exists():
        return {"prior_state_present": False}
    raise DockerFirewallError("Docker firewall backup structure is invalid")


def create_backup() -> Path:
    if BACKUP_ROOT.resolve(strict=False) != BACKUP_ROOT:
        raise DockerFirewallError("Docker firewall backup root contains a symlink")
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BACKUP_ROOT, 0o700)
    metadata = BACKUP_ROOT.stat()
    if BACKUP_ROOT.is_symlink() or metadata.st_uid != 0 or metadata.st_gid != 0:
        raise DockerFirewallError("Docker firewall backup root is unsafe")
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(
        tempfile.mkdtemp(prefix=f"{timestamp}-{secrets.token_hex(4)}-", dir=BACKUP_ROOT)
    )
    os.chmod(backup, 0o700)
    if STATE_PATH.exists():
        _validate_private_file(STATE_PATH)
        shutil.copy2(STATE_PATH, backup / "prior-state.json", follow_symlinks=False)
        os.chmod(backup / "prior-state.json", 0o600)
    else:
        (backup / "prior-state.absent").write_text("absent\n", encoding="ascii")
        os.chmod(backup / "prior-state.absent", 0o600)
    index = _backup_index(backup)
    manifest = {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        **index,
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(backup / "manifest.json", 0o600)
    return backup


def validate_backup(path: Path) -> tuple[Path, bool]:
    try:
        root = BACKUP_ROOT.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DockerFirewallError("Docker firewall backup is outside its root") from exc
    if path.is_symlink() or not resolved.is_dir():
        raise DockerFirewallError("Docker firewall backup path is unsafe")
    manifest_path = resolved / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise DockerFirewallError("Docker firewall backup manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DockerFirewallError("Docker firewall backup manifest is invalid") from exc
    index = _backup_index(resolved)
    expected = {"schema_version": 1, "created_at": manifest.get("created_at"), **index}
    if manifest != expected:
        raise DockerFirewallError("Docker firewall backup integrity check failed")
    return resolved, bool(index["prior_state_present"])


def restore_backup(path: Path) -> bool:
    backup, prior_present = validate_backup(path)
    if prior_present:
        prior = parse_state((backup / "prior-state.json").read_text(encoding="utf-8"))
        atomic_write_state(prior)
        apply_state(prior)
        verify_state(prior)
        return True
    else:
        apply_fail_closed()
        STATE_PATH.unlink(missing_ok=True)
        return False


def build_candidate(ipv4_interface: str, ipv6_interface: str) -> FirewallState:
    require_external_interfaces(ipv4_interface, ipv6_interface)
    ipv4, ipv6, hashes = fetch_ranges()
    return FirewallState(ipv4_interface, ipv6_interface, ipv4, ipv6, hashes)


def refresh(candidate: FirewallState) -> Path:
    prior: FirewallState | None = None
    if STATE_PATH.exists():
        prior = load_state()
        verify_state(prior)
    elif managed_state_present():
        raise DockerFirewallError(
            "managed live rules exist without persistent state; operator review is required"
        )
    backup = create_backup()
    try:
        apply_state(candidate)
        verify_state(candidate)
        atomic_write_state(candidate)
        return backup
    except BaseException as exc:
        try:
            with _defer_termination():
                if prior is not None:
                    atomic_write_state(prior)
                    apply_state(prior)
                else:
                    apply_fail_closed()
        except BaseException as rollback_exc:
            raise DockerFirewallError(
                "Docker firewall refresh and automatic rollback both failed"
            ) from rollback_exc
        if prior is None:
            raise DockerFirewallError(
                "Docker firewall refresh failed; emergency web deny is active"
            ) from exc
        if isinstance(exc, DockerFirewallError):
            raise
        raise DockerFirewallError(
            "Docker firewall refresh failed; prior state restored"
        ) from exc


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DockerFirewallError("run this command as root")


def _backup_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError("backup path must be absolute and normalized")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "refresh"):
        child = subparsers.add_parser(command)
        child.add_argument("--ipv4-interface", required=True)
        child.add_argument("--ipv6-interface", required=True)
        if command == "refresh":
            child.add_argument("--confirm", required=True)
    subparsers.add_parser("apply-state")
    subparsers.add_parser("verify-state")
    subparsers.add_parser("apply-pregate-state")
    subparsers.add_parser("verify-pregate-state")
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--backup", required=True, type=_backup_path)
    rollback.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        _require_root()
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(LOCK_PATH.parent, 0o700)
        lock_metadata = LOCK_PATH.parent.stat()
        if (
            LOCK_PATH.parent.is_symlink()
            or lock_metadata.st_uid != 0
            or lock_metadata.st_gid != 0
        ):
            raise DockerFirewallError("Docker firewall runtime directory is unsafe")
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock_fd)
            raise DockerFirewallError(
                "another Docker firewall operation is active"
            ) from exc
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, _termination_requested)

        if arguments.command in {"plan", "refresh"}:
            ipv4_interface = validate_interface(arguments.ipv4_interface)
            ipv6_interface = validate_interface(arguments.ipv6_interface)
            candidate = build_candidate(ipv4_interface, ipv6_interface)
            PreDnatBackend(4).require_parent_chain()
            PreDnatBackend(6).require_parent_chain()
            NetfilterBackend(4).require_docker_chain()
            NetfilterBackend(6).require_docker_chain()
            if arguments.command == "plan":
                print(
                    f"plan: ipv4_ranges={len(candidate.ipv4_ranges)} "
                    "ipv6_enabled=1 "
                    f"ipv6_ranges={len(candidate.ipv6_ranges)} "
                    "managed_ports=2"
                )
                return 0
            if arguments.confirm != REFRESH_CONFIRMATION:
                raise DockerFirewallError("refresh confirmation phrase is invalid")
            backup = refresh(candidate)
            print(
                "Docker-published web ports now accept only current Cloudflare ranges."
            )
            print(f"Rollback snapshot: {backup}")
            return 0

        if arguments.command == "apply-pregate-state":
            try:
                state = load_state()
                apply_pregate_state(state)
            except BaseException as state_exc:
                try:
                    with _defer_termination():
                        apply_pregate_fail_closed()
                except BaseException as fail_closed_exc:
                    raise DockerFirewallError(
                        "pre-Docker state apply failed and emergency fail-closed rules could not be installed"
                    ) from fail_closed_exc
                raise DockerFirewallError(
                    "pre-Docker state apply failed; emergency pre-DNAT web deny is active"
                ) from state_exc
            print("Persistent pre-Docker Cloudflare firewall state applied.")
            return 0

        if arguments.command == "verify-pregate-state":
            verify_pregate_state(load_state())
            print("Persistent pre-Docker Cloudflare firewall state verified.")
            return 0

        if arguments.command == "apply-state":
            try:
                state = load_state()
                apply_state(state)
            except BaseException as state_exc:
                try:
                    with _defer_termination():
                        apply_fail_closed()
                except BaseException as fail_closed_exc:
                    raise DockerFirewallError(
                        "state apply failed and emergency fail-closed rules could not be installed"
                    ) from fail_closed_exc
                raise DockerFirewallError(
                    "state apply failed; emergency web deny is active"
                ) from state_exc
            print("Persistent Docker Cloudflare firewall state applied.")
            return 0

        if arguments.command == "verify-state":
            verify_state(load_state())
            print("Persistent Docker Cloudflare firewall state verified.")
            return 0

        if arguments.confirm != ROLLBACK_CONFIRMATION:
            raise DockerFirewallError("rollback confirmation phrase is invalid")
        with _defer_termination():
            prior_present = restore_backup(arguments.backup)
        if prior_present:
            print("Prior Docker firewall state restored and verified.")
        else:
            print(
                "No prior persistent state existed; emergency web deny remains active."
            )
        return 0
    except DockerFirewallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
