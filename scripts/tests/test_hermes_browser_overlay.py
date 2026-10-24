from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
PIN = json.loads((ROOT / "infra/hermes/browser-runtime.json").read_text())


class HermesBrowserOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = SimpleNamespace(path=Path("/opt/hermes/tools/chromium-1208"))
        self.current = SimpleNamespace(
            path=Path("/opt/hermes/tools/chromium-" + PIN["version"]),
            binary=Path("/opt/hermes/tools/patched/chrome"),
            version=PIN["version"],
        )
        self.prepare = MagicMock()
        self.installed = MagicMock(side_effect=[self.old, self.current])
        self.lock = MagicMock()
        spec = importlib.util.spec_from_file_location(
            "hermes_browser_overlay", ROOT / "infra/hermes/upgrade_browser.py"
        )
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(
            "sys.modules",
            {
                "pm": SimpleNamespace(),
                "pm.build_operations": SimpleNamespace(prepare_tools=self.prepare),
                "pm.install": SimpleNamespace(installed_package=self.installed),
                "pm.lock": SimpleNamespace(Lockfile=lambda _: self.lock),
                "pm.paths": SimpleNamespace(lockfile_path=lambda: Path("/tmp/lock.json")),
                "pm.store": SimpleNamespace(current_target=lambda: "linux-x64"),
            },
        ):
            spec.loader.exec_module(self.module)
        self.addCleanup(patch.stopall)
        patch.object(self.module.Path, "read_text", return_value=json.dumps(PIN)).start()
        self.remove = patch.object(self.module.shutil, "rmtree").start()
        self.version = patch.object(
            self.module.subprocess,
            "check_output",
            return_value="Google Chrome for Testing " + PIN["version"],
        ).start()

    def test_installs_recorded_pin_before_removing_old_browser(self) -> None:
        self.module.main()
        self.lock.set_pin.assert_called_once_with(
            "chromium", PIN["version"], PIN["artifacts"]
        )
        self.lock.save.assert_called_once()
        self.prepare.assert_called_once_with(
            ["chromium"], out=Path("/opt/hermes/tools"), target="linux-x64"
        )
        self.version.assert_called_once_with([str(self.current.binary), "--version"], text=True)
        self.remove.assert_called_once_with(self.old.path)

    def test_rejects_unreviewed_architecture_before_changing_pin(self) -> None:
        with patch.object(self.module, "current_target", return_value="linux-arm64"):
            with self.assertRaisesRegex(RuntimeError, "No reviewed browser artifact"):
                self.module.main()
        self.lock.set_pin.assert_not_called()
        self.remove.assert_not_called()

    def test_keeps_old_browser_if_discovery_fails(self) -> None:
        self.installed.side_effect = [self.old, None]
        with self.assertRaisesRegex(RuntimeError, "not discoverable"):
            self.module.main()
        self.remove.assert_not_called()

    def test_keeps_old_browser_if_binary_version_disagrees(self) -> None:
        self.version.return_value = "Chromium 145.0.7632.6"
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.module.main()
        self.remove.assert_not_called()

    def test_never_removes_a_path_outside_the_bundled_tool_store(self) -> None:
        self.old.path = Path("/somewhere/else")
        with self.assertRaises(ValueError):
            self.module.main()
        self.remove.assert_not_called()

    def test_first_install_has_no_old_tree_to_remove(self) -> None:
        self.installed.side_effect = [None, self.current]
        self.module.main()
        self.remove.assert_not_called()

    def test_same_entry_is_not_removed(self) -> None:
        self.installed.side_effect = [self.current, self.current]
        self.module.main()
        self.remove.assert_not_called()

    def test_rejects_browser_hidden_by_the_profile_volume(self) -> None:
        self.current.path = Path("/opt/data/tools/chromium-patched")
        with self.assertRaisesRegex(RuntimeError, "not in the shared image"):
            self.module.main()
        self.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
