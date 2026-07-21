#!/usr/bin/env python3
"""Restrict a UFW-protected origin to Cloudflare's current proxy ranges.

The apply path is deliberately additive before it is subtractive: it validates
Cloudflare's live lists, preflights every UFW command, verifies the selected SSH
allow mode, adds and verifies all current Cloudflare rules, and only then removes
public or stale managed web rules.  SSH may be restricted to a stable CIDR, or
left globally reachable for key-only authentication when admin addresses are
dynamic.  A complete /etc/ufw snapshot is restored and reloaded automatically if
any post-backup step fails.
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
ALLOWED_FETCH_HOST = "www.cloudflare.com"
MANAGED_COMMENT = "bumpabestie-cloudflare-origin"
INBOUND_AUTH_ACTIONS = frozenset({"ALLOW IN", "LIMIT IN"})
SSH_COMMENT = "bumpabestie-admin-ssh"
DEFAULT_BACKUP_ROOT = Path("/var/lib/bumpabestie/firewall-backups")
DEFAULT_UFW_ROOT = Path("/etc/ufw")
DEFAULT_UFW_EXECUTABLE = Path("/usr/sbin/ufw")
DEFAULT_UFW_DEFAULTS = Path("/etc/default/ufw")
LOCK_PATH = Path("/run/lock/bumpabestie-cloudflare-ufw.lock")
MAX_RESPONSE_BYTES = 64 * 1024
MAX_RANGES_PER_FAMILY = 256
CONFIRMATION = "restrict-origin-to-cloudflare"
WEB_PORTS = (80, 443)


class FirewallError(RuntimeError):
    """An expected fail-closed validation or firewall error."""


def _termination_requested(signum: int, _frame: object) -> None:
    raise FirewallError(f"firewall operation interrupted by signal {signum}")


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


@dataclasses.dataclass(frozen=True)
class Rule:
    number: int
    target: str
    source: str
    comment: str
    action: str = "ALLOW IN"
    address_family: int = 4

    @property
    def port(self) -> int | None:
        match = re.fullmatch(r"(80|443|22)/tcp", self.target)
        return int(match.group(1)) if match else None

    @property
    def web_ports(self) -> frozenset[int]:
        match = re.fullmatch(r"([0-9,:]+)/tcp", self.target)
        if not match:
            return frozenset()
        ports: set[int] = set()
        for component in match.group(1).split(","):
            if ":" in component:
                lower_text, upper_text = component.split(":", 1)
                lower, upper = int(lower_text), int(upper_text)
                for port in WEB_PORTS:
                    if lower <= port <= upper:
                        ports.add(port)
            else:
                value = int(component)
                if value in WEB_PORTS:
                    ports.add(value)
        return frozenset(ports)

    @property
    def normalized_source(self) -> str:
        if self.source == "Anywhere":
            return self.source
        try:
            return ipaddress.ip_network(self.source, strict=True).with_prefixlen
        except ValueError:
            return self.source


@dataclasses.dataclass(frozen=True)
class RuleAssessment:
    missing: tuple[tuple[int, str], ...]
    broad_rule_numbers: tuple[int, ...]
    stale_managed_numbers: tuple[int, ...]
    unexpected_web_rules: tuple[Rule, ...]


def _https_get(url: str, *, redirects_remaining: int = 2) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_FETCH_HOST:
        raise FirewallError("Cloudflare range URL left the pinned HTTPS origin")
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=15, context=context
    )
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    try:
        connection.request(
            "GET",
            path,
            headers={"User-Agent": "BumpaBestie-origin-firewall/1"},
        )
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            if redirects_remaining <= 0:
                raise FirewallError("too many Cloudflare range redirects")
            location = response.getheader("Location")
            if not location:
                raise FirewallError("Cloudflare range redirect has no location")
            return _https_get(
                urljoin(url, location), redirects_remaining=redirects_remaining - 1
            )
        if response.status != 200:
            raise FirewallError(
                f"Cloudflare range endpoint returned HTTP {response.status}"
            )
        content_type = (response.getheader("Content-Type") or "").lower()
        if not content_type.startswith("text/plain"):
            raise FirewallError("Cloudflare range endpoint was not text/plain")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise FirewallError("Cloudflare range response exceeded the size limit")
        return payload
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise FirewallError(
            "unable to fetch Cloudflare ranges over verified TLS"
        ) from exc
    finally:
        connection.close()


def validate_ranges(payload: bytes, family: int) -> tuple[str, ...]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FirewallError("Cloudflare range response was not ASCII") from exc

    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not raw_lines:
        raise FirewallError(f"Cloudflare IPv{family} range list was empty")
    if len(raw_lines) > MAX_RANGES_PER_FAMILY:
        raise FirewallError(f"Cloudflare IPv{family} range list was implausibly large")

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    seen: set[str] = set()
    for raw in raw_lines:
        if re.search(r"\s", raw):
            raise FirewallError("Cloudflare range list contained unexpected whitespace")
        try:
            network = ipaddress.ip_network(raw, strict=True)
        except ValueError as exc:
            raise FirewallError(
                "Cloudflare range list contained malformed CIDR"
            ) from exc
        if network.version != family:
            raise FirewallError(f"Cloudflare IPv{family} list mixed address families")
        canonical = network.with_prefixlen
        if raw.lower() != canonical.lower():
            raise FirewallError("Cloudflare range list contained non-canonical CIDR")
        if not network.is_global or network.prefixlen == 0:
            raise FirewallError("Cloudflare range list contained a non-global network")
        if canonical in seen:
            raise FirewallError("Cloudflare range list contained a duplicate CIDR")
        seen.add(canonical)
        networks.append(network)

    for index, network in enumerate(networks):
        if any(network.overlaps(other) for other in networks[index + 1 :]):
            raise FirewallError("Cloudflare range list contained overlapping CIDRs")

    return tuple(
        network.with_prefixlen
        for network in sorted(
            networks, key=lambda item: (int(item.network_address), item.prefixlen)
        )
    )


def fetch_cloudflare_ranges() -> tuple[
    tuple[str, ...], tuple[str, ...], dict[str, str]
]:
    ipv4_payload = _https_get(IPV4_URL)
    ipv6_payload = _https_get(IPV6_URL)
    ipv4 = validate_ranges(ipv4_payload, 4)
    ipv6 = validate_ranges(ipv6_payload, 6)
    hashes = {
        "ipv4_sha256": hashlib.sha256(ipv4_payload).hexdigest(),
        "ipv6_sha256": hashlib.sha256(ipv6_payload).hexdigest(),
    }
    return ipv4, ipv6, hashes


def normalize_cidr(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise FirewallError("SSH source must be one canonical CIDR") from exc
    if not network.is_global or network.prefixlen == 0:
        raise FirewallError("SSH source must be a non-empty global CIDR")
    canonical = network.with_prefixlen
    if value.lower() != canonical.lower():
        raise FirewallError("SSH source must use canonical CIDR notation")
    return canonical


def parse_numbered_rules(output: str) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for raw_line in output.splitlines():
        match = re.match(
            r"^\[\s*(\d+)\]\s+(.+?)\s+"
            r"(ALLOW IN|DENY IN|REJECT IN|LIMIT IN)\s+(.+?)\s*$",
            raw_line,
        )
        if not match:
            continue
        number = int(match.group(1))
        raw_target = match.group(2).strip()
        target = re.sub(r"\s+\(v6\)$", "", raw_target)
        action = match.group(3)
        right = match.group(4).strip()
        source_part, separator, comment_part = right.partition("#")
        raw_source = source_part.strip()
        source = re.sub(r"\s+\(v6\)$", "", raw_source)
        comment = comment_part.strip().strip("'\"") if separator else ""
        address_family = (
            6 if raw_target.endswith("(v6)") or raw_source.endswith("(v6)") else 4
        )
        rules.append(Rule(number, target, source, comment, action, address_family))
    return tuple(rules)


def desired_rule_keys(ranges: Iterable[str]) -> frozenset[tuple[int, str]]:
    return frozenset(
        (port, ipaddress.ip_network(source, strict=True).with_prefixlen)
        for source in ranges
        for port in WEB_PORTS
    )


def assess_rules(
    rules: Sequence[Rule], desired: frozenset[tuple[int, str]]
) -> RuleAssessment:
    present: set[tuple[int, str]] = set()
    broad: list[int] = []
    stale: list[int] = []
    unexpected: list[Rule] = []
    for rule in rules:
        web_ports = rule.web_ports
        if not web_ports:
            if rule.action in INBOUND_AUTH_ACTIONS and rule.target != "22/tcp":
                unexpected.append(rule)
            continue
        if len(web_ports) != 1 or rule.port not in WEB_PORTS:
            unexpected.append(rule)
            continue
        if rule.action != "ALLOW IN":
            unexpected.append(rule)
            continue
        key = (rule.port, rule.normalized_source)
        if rule.normalized_source == "Anywhere":
            broad.append(rule.number)
            continue
        if key in desired:
            present.add(key)
            continue
        if rule.comment == MANAGED_COMMENT:
            stale.append(rule.number)
        else:
            unexpected.append(rule)
    return RuleAssessment(
        missing=tuple(sorted(desired - present)),
        broad_rule_numbers=tuple(sorted(broad, reverse=True)),
        stale_managed_numbers=tuple(sorted(stale, reverse=True)),
        unexpected_web_rules=tuple(unexpected),
    )


def _ssh_authorization_rules(rules: Sequence[Rule]) -> tuple[Rule, ...]:
    return tuple(
        rule
        for rule in rules
        if rule.action in INBOUND_AUTH_ACTIONS and rule.port == 22
    )


def _global_ssh_family(rule: Rule) -> int | None:
    if rule.address_family in {4, 6}:
        return rule.address_family
    return None


def ssh_rule_present(rules: Sequence[Rule], ssh_cidr: str | None) -> bool:
    authorizations = _ssh_authorization_rules(rules)
    if ssh_cidr is not None:
        return any(
            rule.action == "ALLOW IN" and rule.normalized_source == ssh_cidr
            for rule in authorizations
        )

    global_rules = [
        rule
        for rule in authorizations
        if rule.action == "ALLOW IN" and rule.normalized_source == "Anywhere"
    ]
    return len(global_rules) == 2 and {
        _global_ssh_family(rule) for rule in global_rules
    } == {4, 6}


def unexpected_ssh_rules(
    rules: Sequence[Rule], ssh_cidr: str | None
) -> tuple[Rule, ...]:
    authorizations = _ssh_authorization_rules(rules)
    if ssh_cidr is not None:
        return tuple(
            rule
            for rule in authorizations
            if rule.action != "ALLOW IN" or rule.normalized_source != ssh_cidr
        )

    unexpected: list[Rule] = []
    seen_families: set[int] = set()
    for rule in authorizations:
        family = _global_ssh_family(rule)
        if (
            rule.action != "ALLOW IN"
            or rule.normalized_source != "Anywhere"
            or family not in {4, 6}
            or family in seen_families
        ):
            unexpected.append(rule)
            continue
        seen_families.add(family)
    return tuple(unexpected)


class UfwClient:
    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def _run(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                [str(self.executable), *arguments],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
                env={
                    "HOME": "/root",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                },
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FirewallError(f"UFW command failed: {' '.join(arguments)}") from exc
        return result.stdout

    def numbered_status(self) -> str:
        output = self._run("status", "numbered")
        if not output.startswith("Status: active"):
            raise FirewallError("UFW must already be active")
        return output

    def verbose_status(self) -> str:
        output = self._run("status", "verbose")
        if "Default: deny (incoming)" not in output:
            raise FirewallError("UFW default incoming policy must already be deny")
        return output

    def require_ipv6(self) -> None:
        try:
            content = DEFAULT_UFW_DEFAULTS.read_text(encoding="utf-8")
        except OSError as exc:
            raise FirewallError("unable to validate UFW IPv6 configuration") from exc
        values = [
            line.split("=", 1)[1].strip().lower()
            for line in content.splitlines()
            if line.strip().startswith("IPV6=")
        ]
        if values != ["yes"]:
            raise FirewallError("UFW IPv6 support must be enabled before hardening")

    def preflight_allow(self, source: str, port: int, comment: str) -> None:
        self._run(
            "--dry-run",
            "allow",
            "from",
            source,
            "to",
            "any",
            "port",
            str(port),
            "proto",
            "tcp",
            "comment",
            comment,
        )

    def preflight_global_ssh(self, comment: str) -> None:
        self._run("--dry-run", "allow", "22/tcp", "comment", comment)

    def allow(self, source: str, port: int, comment: str) -> None:
        self._run(
            "allow",
            "from",
            source,
            "to",
            "any",
            "port",
            str(port),
            "proto",
            "tcp",
            "comment",
            comment,
        )

    def allow_global_ssh(self, comment: str) -> None:
        self._run("allow", "22/tcp", "comment", comment)

    def delete_number(self, number: int) -> None:
        self._run("--force", "delete", str(number))

    def reload(self) -> None:
        self._run("--force", "reload")


def _file_index(root: Path) -> dict[str, dict[str, int | str]]:
    index: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise FirewallError("UFW snapshot contained an unsafe filesystem entry")
        relative = path.relative_to(root).as_posix()
        record: dict[str, int | str] = {
            "kind": "directory" if path.is_dir() else "file",
            "mode": path.stat().st_mode & 0o777,
        }
        if path.is_file():
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        index[relative] = record
    return index


class BackupManager:
    def __init__(self, backup_root: Path, ufw_root: Path, ufw: UfwClient) -> None:
        self.backup_root = backup_root
        self.ufw_root = ufw_root
        self.ufw = ufw

    def _validate_roots(self) -> None:
        if not self.ufw_root.is_absolute() or self.ufw_root != DEFAULT_UFW_ROOT:
            raise FirewallError("UFW configuration root must be /etc/ufw")
        if not self.ufw_root.is_dir() or self.ufw_root.is_symlink():
            raise FirewallError("/etc/ufw is missing or unsafe")
        if (
            not self.backup_root.is_absolute()
            or self.backup_root.is_symlink()
            or self.backup_root.resolve(strict=False) != self.backup_root
        ):
            raise FirewallError(
                "firewall backup root must be an absolute real directory"
            )

    def create(
        self,
        *,
        numbered_status: str,
        verbose_status: str,
        ssh_cidr: str | None,
        ranges: Sequence[str],
        source_hashes: dict[str, str],
    ) -> Path:
        self._validate_roots()
        self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.backup_root, 0o700)
        root_stat = self.backup_root.stat()
        if root_stat.st_uid != 0 or root_stat.st_gid != 0:
            raise FirewallError("firewall backup root must be owned by root")
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(
            tempfile.mkdtemp(
                prefix=f"{timestamp}-{secrets.token_hex(4)}-", dir=self.backup_root
            )
        )
        os.chmod(backup, 0o700)
        snapshot = backup / "etc-ufw"
        shutil.copytree(self.ufw_root, snapshot, symlinks=False)
        index = _file_index(snapshot)
        manifest = {
            "schema_version": 1,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ssh_mode": "restricted_cidr" if ssh_cidr else "global_key_only",
            "ssh_cidr": ssh_cidr,
            "range_sources": [IPV4_URL, IPV6_URL],
            "range_count": len(ranges),
            "source_hashes": source_hashes,
            "snapshot_index": index,
        }
        for name, content in {
            "status-numbered.txt": numbered_status,
            "status-verbose.txt": verbose_status,
            "manifest.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            ".bumpabestie-ufw-backup-v1": "BumpaBestie UFW backup v1\n",
        }.items():
            target = backup / name
            target.write_text(content, encoding="utf-8")
            os.chmod(target, 0o600)
        return backup

    def validate(self, backup: Path) -> Path:
        self._validate_roots()
        try:
            resolved_root = self.backup_root.resolve(strict=True)
            resolved = backup.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise FirewallError("backup is outside the configured backup root") from exc
        if (
            backup.is_symlink()
            or not (resolved / ".bumpabestie-ufw-backup-v1").is_file()
        ):
            raise FirewallError("firewall backup marker is missing or unsafe")
        manifest_path = resolved / "manifest.json"
        snapshot = resolved / "etc-ufw"
        if manifest_path.is_symlink() or snapshot.is_symlink() or not snapshot.is_dir():
            raise FirewallError("firewall backup structure is unsafe")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FirewallError("firewall backup manifest is invalid") from exc
        if manifest.get("schema_version") != 1:
            raise FirewallError("firewall backup schema is unsupported")
        if manifest.get("snapshot_index") != _file_index(snapshot):
            raise FirewallError("firewall backup integrity validation failed")
        return snapshot

    def restore(self, backup: Path) -> None:
        snapshot = self.validate(backup)
        parent = self.ufw_root.parent
        staged = parent / f".ufw.restore.{os.getpid()}.{secrets.token_hex(4)}"
        displaced = parent / f".ufw.failed.{os.getpid()}.{secrets.token_hex(4)}"
        shutil.copytree(snapshot, staged, symlinks=False)
        try:
            os.replace(self.ufw_root, displaced)
            try:
                os.replace(staged, self.ufw_root)
            except BaseException:
                os.replace(displaced, self.ufw_root)
                raise
            try:
                self.ufw.reload()
            except Exception:
                failed_restore = parent / f".ufw.restore-failed.{os.getpid()}"
                os.replace(self.ufw_root, failed_restore)
                os.replace(displaced, self.ufw_root)
                self.ufw.reload()
                shutil.rmtree(failed_restore, ignore_errors=True)
                raise
            shutil.rmtree(displaced)
        finally:
            shutil.rmtree(staged, ignore_errors=True)


def _current_rules(ufw: UfwClient) -> tuple[Rule, ...]:
    return parse_numbered_rules(ufw.numbered_status())


def _verify_compliant(
    ufw: UfwClient, desired: frozenset[tuple[int, str]], ssh_cidr: str | None
) -> None:
    rules = _current_rules(ufw)
    assessment = assess_rules(rules, desired)
    if not ssh_rule_present(rules, ssh_cidr):
        raise FirewallError("selected SSH allow rule set is incomplete")
    if unexpected_ssh_rules(rules, ssh_cidr):
        raise FirewallError("an extra or unmanaged SSH allow rule remains")
    if assessment.missing:
        raise FirewallError("one or more current Cloudflare allow rules are absent")
    if assessment.broad_rule_numbers:
        raise FirewallError("a public Anywhere web allow rule remains")
    if assessment.stale_managed_numbers:
        raise FirewallError("a stale managed Cloudflare rule remains")
    if assessment.unexpected_web_rules:
        raise FirewallError("an unknown or unmanaged inbound allow rule remains")
    ufw.verbose_status()


def apply_hardening(
    ufw: UfwClient,
    backups: BackupManager,
    *,
    ranges: Sequence[str],
    source_hashes: dict[str, str],
    ssh_cidr: str | None,
    plan_only: bool,
) -> tuple[str, Path | None]:
    desired = desired_rule_keys(ranges)
    numbered = ufw.numbered_status()
    verbose = ufw.verbose_status()
    if any(ipaddress.ip_network(source, strict=True).version == 6 for source in ranges):
        ufw.require_ipv6()
    rules = parse_numbered_rules(numbered)
    assessment = assess_rules(rules, desired)
    if assessment.unexpected_web_rules:
        raise FirewallError(
            "unknown or unmanaged inbound allow rules require operator review"
        )
    if unexpected_ssh_rules(rules, ssh_cidr):
        raise FirewallError(
            "extra or unmanaged SSH allow rules require operator review"
        )
    needs_ssh = not ssh_rule_present(rules, ssh_cidr)

    if needs_ssh:
        if ssh_cidr is None:
            ufw.preflight_global_ssh(SSH_COMMENT)
        else:
            ufw.preflight_allow(ssh_cidr, 22, SSH_COMMENT)
    for port, source in assessment.missing:
        ufw.preflight_allow(source, port, MANAGED_COMMENT)

    deletions = set(assessment.broad_rule_numbers) | set(
        assessment.stale_managed_numbers
    )
    if plan_only:
        return (
            f"plan: ssh_add={int(needs_ssh)} cloudflare_add={len(assessment.missing)} "
            f"web_delete={len(deletions)}",
            None,
        )
    if not needs_ssh and not assessment.missing and not deletions:
        _verify_compliant(ufw, desired, ssh_cidr)
        return "already compliant; no firewall changes made", None

    backup = backups.create(
        numbered_status=numbered,
        verbose_status=verbose,
        ssh_cidr=ssh_cidr,
        ranges=ranges,
        source_hashes=source_hashes,
    )
    mutation_started = False
    try:
        if needs_ssh:
            mutation_started = True
            if ssh_cidr is None:
                ufw.allow_global_ssh(SSH_COMMENT)
            else:
                ufw.allow(ssh_cidr, 22, SSH_COMMENT)
            if not ssh_rule_present(_current_rules(ufw), ssh_cidr):
                raise FirewallError("SSH allow rule set did not become active")

        for port, source in assessment.missing:
            mutation_started = True
            ufw.allow(source, port, MANAGED_COMMENT)

        after_add = assess_rules(_current_rules(ufw), desired)
        if after_add.missing:
            raise FirewallError("Cloudflare rules were not complete before deletion")

        delete_numbers = sorted(
            set(after_add.broad_rule_numbers) | set(after_add.stale_managed_numbers),
            reverse=True,
        )
        for number in delete_numbers:
            mutation_started = True
            ufw.delete_number(number)

        ufw.reload()
        _verify_compliant(ufw, desired, ssh_cidr)
        ssh_mode = "restricted CIDR" if ssh_cidr else "global key-only"
        return (
            f"firewall restricted to current Cloudflare ranges; SSH mode: {ssh_mode}",
            backup,
        )
    except BaseException as exc:
        if mutation_started:
            try:
                with _defer_termination():
                    backups.restore(backup)
            except BaseException as rollback_exc:
                raise FirewallError(
                    "firewall hardening failed and automatic rollback also failed"
                ) from rollback_exc
        if isinstance(exc, FirewallError):
            raise
        raise FirewallError(
            "firewall hardening failed; prior rules were restored"
        ) from exc


def _require_root() -> None:
    if os.geteuid() != 0:
        raise FirewallError("run this command as root")


def _ufw_client() -> UfwClient:
    executable = DEFAULT_UFW_EXECUTABLE
    try:
        executable_stat = executable.stat()
    except OSError as exc:
        raise FirewallError("/usr/sbin/ufw is missing") from exc
    if (
        not executable.is_file()
        or executable.is_symlink()
        or executable_stat.st_uid != 0
        or executable_stat.st_mode & 0o022
    ):
        raise FirewallError("/usr/sbin/ufw is not a trusted root-owned executable")
    return UfwClient(executable)


def _backup_root(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError(
            "backup root must be an absolute normalized path"
        )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "--ssh-cidr",
            help=(
                "restrict SSH to this stable CIDR; omit for globally reachable "
                "key-only SSH"
            ),
        )
        child.add_argument(
            "--backup-root", type=_backup_root, default=DEFAULT_BACKUP_ROOT
        )
        if command == "apply":
            child.add_argument("--confirm", required=True)
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--backup", type=_backup_root, required=True)
    rollback.add_argument(
        "--backup-root", type=_backup_root, default=DEFAULT_BACKUP_ROOT
    )
    rollback.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        _require_root()
        ufw = _ufw_client()
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock_fd)
            raise FirewallError(
                "another firewall operation is already running"
            ) from exc

        backups = BackupManager(arguments.backup_root, DEFAULT_UFW_ROOT, ufw)
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, _termination_requested)
        if arguments.command == "rollback":
            if arguments.confirm != "restore-previous-ufw-rules":
                raise FirewallError("rollback confirmation phrase is invalid")
            with _defer_termination():
                backups.restore(arguments.backup)
            print("Restored the validated UFW snapshot and reloaded the firewall.")
            return 0

        ssh_cidr = normalize_cidr(arguments.ssh_cidr) if arguments.ssh_cidr else None
        ipv4, ipv6, source_hashes = fetch_cloudflare_ranges()
        if arguments.command == "apply" and arguments.confirm != CONFIRMATION:
            raise FirewallError("apply confirmation phrase is invalid")
        result, backup = apply_hardening(
            ufw,
            backups,
            ranges=(*ipv4, *ipv6),
            source_hashes=source_hashes,
            ssh_cidr=ssh_cidr,
            plan_only=arguments.command == "plan",
        )
        print(result)
        if backup is not None:
            print(f"Rollback snapshot: {backup}")
        return 0
    except FirewallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
