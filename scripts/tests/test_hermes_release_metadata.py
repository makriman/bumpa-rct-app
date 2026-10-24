from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release_metadata", ROOT / "infra/hermes/reconcile_package_version.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HermesReleaseMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stamp = dict(
            commit=module.COMMIT,
            baseVersion=module.VERSION,
            displayVersion=module.VERSION,
            dirty=False,
            source="ci",
            distribution="docker",
        )
        (self.root / "install-stamp.json").write_text(json.dumps(self.stamp))
        self.old = self.root / "hermes_agent-0.0.0.dist-info"
        self.old.mkdir()
        self.metadata = "Metadata-Version: 2.3\nName: hermes-agent\nVersion: 0.0.0\nRequires-Python: >=3.11\n"
        (self.old / "METADATA").write_text(self.metadata)
        (self.old / "entry_points.txt").write_text("entry points preserved")

    def test_uses_verified_release_and_preserves_package_contract(self) -> None:
        module.reconcile(self.root)
        target = self.root / f"hermes_agent-{module.VERSION}.dist-info"
        self.assertFalse(self.old.exists())
        self.assertEqual(
            (target / "METADATA").read_text(),
            self.metadata.replace("Version: 0.0.0", "Version: " + module.VERSION),
        )
        self.assertEqual(
            (target / "entry_points.txt").read_text(), "entry points preserved"
        )

    def test_rejects_different_or_dirty_release_without_modification(self) -> None:
        for key, value in [
            ("commit", "unknown"),
            ("baseVersion", "0.15.0"),
            ("dirty", True),
            ("source", "local"),
        ]:
            with self.subTest(key=key):
                (self.root / "install-stamp.json").write_text(
                    json.dumps({**self.stamp, key: value})
                )
                with self.assertRaisesRegex(RuntimeError, "provenance"):
                    module.reconcile(self.root)
                self.assertEqual((self.old / "METADATA").read_text(), self.metadata)

    def test_rejects_wheel_record_requiring_a_different_reconciliation(self) -> None:
        (self.old / "RECORD").write_text("wheel record")
        with self.assertRaisesRegex(RuntimeError, "layout"):
            module.reconcile(self.root)
        self.assertEqual((self.old / "METADATA").read_text(), self.metadata)

    def test_rejects_conflicting_metadata(self) -> None:
        (self.root / f"hermes_agent-{module.VERSION}.dist-info").mkdir()
        with self.assertRaisesRegex(RuntimeError, "Conflicting"):
            module.reconcile(self.root)
        self.assertEqual((self.old / "METADATA").read_text(), self.metadata)
