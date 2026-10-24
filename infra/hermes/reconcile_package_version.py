"""Reconcile upstream's placeholder distribution metadata with its CI release stamp.

The immutable upstream payload is a bootstrap installation, without a wheel
RECORD. Refuse any different release or packaging layout rather than guessing.
"""

import json
import shutil
from pathlib import Path

COMMIT = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"
VERSION = "0.22.5"


def reconcile(root: Path) -> None:
    stamp = json.loads((root / "install-stamp.json").read_text())
    expected = {
        "commit": COMMIT,
        "baseVersion": VERSION,
        "displayVersion": VERSION,
        "dirty": False,
        "source": "ci",
        "distribution": "docker",
    }
    if any(stamp.get(key) != value for key, value in expected.items()):
        raise RuntimeError(
            "Upstream release provenance differs from the reviewed payload"
        )
    old = root / "hermes_agent-0.0.0.dist-info"
    if {p.name for p in old.iterdir()} != {"METADATA", "entry_points.txt"}:
        raise RuntimeError("Unreviewed upstream metadata layout")
    metadata = (old / "METADATA").read_text()
    if (
        metadata.count("\nVersion: 0.0.0\n") != 1
        or "\nName: hermes-agent\n" not in metadata
    ):
        raise RuntimeError("Unexpected upstream package identity")
    target = root / f"hermes_agent-{VERSION}.dist-info"
    if target.exists():
        raise RuntimeError("Conflicting distribution metadata")
    (old / "METADATA").write_text(
        metadata.replace("\nVersion: 0.0.0\n", f"\nVersion: {VERSION}\n")
    )
    shutil.move(str(old), str(target))


def main() -> None:
    # Exercise the vulnerable boundary before recording the real release identity.
    from types import SimpleNamespace
    from hermes_cli.web_server_boundary import ws_host_origin_reason

    state = SimpleNamespace(bound_host="127.0.0.1", trusted_public_hosts=frozenset())
    for host, origin, refused in [
        ("127.0.0.1:9119", "http://127.0.0.1:9119", False),
        ("attacker.example", "http://attacker.example", True),
        ("127.0.0.1:9119", "http://attacker.example", True),
        ("127.0.0.1:9119", "http://[::1", True),
    ]:
        reason = ws_host_origin_reason(
            SimpleNamespace(headers={"host": host, "origin": origin}), state
        )
        assert (reason is not None) == refused, (host, origin, reason)
    reconcile(Path("/opt/hermes"))


if __name__ == "__main__":
    main()
