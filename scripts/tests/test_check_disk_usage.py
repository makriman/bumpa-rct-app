from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parents[1] / "check_disk_usage.py"
SPEC = importlib.util.spec_from_file_location("check_disk_usage", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
disk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = disk
SPEC.loader.exec_module(disk)


class DiskUsageTest(unittest.TestCase):
    def statvfs(
        self,
        *,
        blocks: int = 100,
        free_blocks: int = 16,
        available_blocks: int = 15,
        files: int = 100,
        free_files: int = 80,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            f_blocks=blocks,
            f_bfree=free_blocks,
            f_bavail=available_blocks,
            f_bsize=4096,
            f_frsize=4096,
            f_files=files,
            f_ffree=free_files,
            f_favail=free_files,
        )

    @patch.object(disk.socket, "gethostname", return_value="test-host")
    @patch.object(disk.os, "stat")
    @patch.object(disk.os, "statvfs")
    def test_threshold_boundary_emits_alert(self, statvfs_mock, stat_mock, _hostname_mock) -> None:
        stat_mock.return_value = SimpleNamespace(st_dev=7)
        statvfs_mock.return_value = self.statvfs()

        event, exit_code = disk.build_event(
            ["/"],
            85,
            datetime(2026, 7, 12, tzinfo=timezone.utc),  # noqa: UP017
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(event["status"], "alert")
        self.assertEqual(event["near_full_device_ids"], [7])
        filesystem = event["filesystems"][0]
        self.assertEqual(filesystem["blocks"]["used_percent"], 85)
        self.assertEqual(event["checked_at"], "2026-07-12T00:00:00Z")

    @patch.object(disk.os, "stat")
    @patch.object(disk.os, "statvfs")
    def test_aliases_on_same_device_are_checked_once(self, statvfs_mock, stat_mock) -> None:
        stat_mock.return_value = SimpleNamespace(st_dev=9)
        statvfs_mock.return_value = self.statvfs(free_blocks=60, available_blocks=60)

        filesystems, errors = disk.inspect_filesystems(["/", "/var"])

        self.assertEqual(errors, [])
        self.assertEqual(len(filesystems), 1)
        self.assertEqual(
            filesystems[0].aliases,
            [str(Path("/").resolve()), str(Path("/var").resolve())],
        )
        self.assertEqual(statvfs_mock.call_count, 1)

    @patch.object(disk.os, "stat", side_effect=FileNotFoundError())
    def test_uncheckable_path_is_an_operational_error(self, _stat_mock) -> None:
        event, exit_code = disk.build_event(["/missing"], 85)

        self.assertEqual(exit_code, 2)
        self.assertEqual(event["status"], "error")
        self.assertEqual(event["errors"][0]["error"], "FileNotFoundError")

    def test_inode_exhaustion_triggers_alert(self) -> None:
        filesystem = disk.FilesystemUsage(
            aliases=["/data"],
            blocks=disk.ResourceUsage(available=90, total=100, used=10, used_percent=10),
            device_id=12,
            inodes=disk.ResourceUsage(available=1, total=100, used=99, used_percent=99),
        )

        self.assertTrue(disk.is_near_full(filesystem, 90))

    def test_hook_receives_json_on_stdin_without_argv_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "event.json"
            hook_path = Path(directory) / "hook"
            hook_path.write_text(
                f'#!/bin/sh\n[ "$#" -eq 0 ]\n[ -z "${{JWT_SECRET:-}}" ]\ncat > {output_path}\n',
                encoding="utf-8",
            )
            hook_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            event_json = json.dumps({"event": "disk_usage_check", "status": "alert"})

            with patch.dict(os.environ, {"JWT_SECRET": "must-not-be-exported"}):
                self.assertTrue(disk.invoke_alert_hook(str(hook_path), event_json))
            self.assertEqual(json.loads(output_path.read_text()), json.loads(event_json))

    def test_symlink_hook_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "target"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            link = Path(directory) / "hook"
            link.symlink_to(executable)

            self.assertFalse(disk.invoke_alert_hook(str(link), "{}"))

    def test_paths_from_environment_use_platform_separator(self) -> None:
        with patch.dict(os.environ, {"BUMPABESTIE_DISK_PATHS": "/:/data"}):
            self.assertEqual(disk.parse_paths(None), ["/", "/data"])

    def test_invalid_threshold_returns_configuration_error_json(self) -> None:
        with patch("builtins.print") as print_mock:
            exit_code = disk.main(["--threshold-percent", "0", "--path", "/"])

        self.assertEqual(exit_code, 2)
        output = json.loads(print_mock.call_args.args[0])
        self.assertEqual(output["status"], "configuration_error")


if __name__ == "__main__":
    unittest.main()
