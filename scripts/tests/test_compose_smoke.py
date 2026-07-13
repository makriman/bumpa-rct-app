from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).parents[2]
COMPOSE_SMOKE_SCRIPT = ROOT_DIR / "scripts" / "compose_smoke.sh"

FAKE_DOCKER = """#!/usr/bin/env bash
set -Eeuo pipefail
printf 'PROJECT=%s ARGS=' "${COMPOSE_PROJECT_NAME:-}"
printf '%q ' "$@"
printf '\\n'
"""

FAKE_SUCCESS = """#!/usr/bin/env bash
set -Eeuo pipefail
exit 0
"""


class ComposeSmokeIsolationTest(unittest.TestCase):
    def _fixture(self, directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        scripts = root / "scripts"
        scripts.mkdir()
        shutil.copy2(COMPOSE_SMOKE_SCRIPT, scripts / "compose_smoke.sh")
        for name in ("smoke_test.sh", "local_e2e.sh"):
            path = scripts / name
            path.write_text(FAKE_SUCCESS, encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        (root / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")

        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return root, bin_dir

    def _run(
        self,
        root: Path,
        bin_dir: Path,
        *,
        project_name: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        }
        if project_name is None:
            environment.pop("COMPOSE_PROJECT_NAME", None)
        else:
            environment["COMPOSE_PROJECT_NAME"] = project_name
        return subprocess.run(  # noqa: S603 - copied repository script
            [str(root / "scripts" / "compose_smoke.sh")],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_default_run_uses_disposable_project_and_removes_its_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, bin_dir = self._fixture(directory)

            result = self._run(root, bin_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [line for line in result.stdout.splitlines() if line.startswith("PROJECT=")]
            self.assertGreaterEqual(len(calls), 7)
            projects = {line.split(" ", 1)[0].removeprefix("PROJECT=") for line in calls}
            self.assertEqual(len(projects), 1)
            self.assertTrue(projects.pop().startswith("bumpabestie-smoke-"))
            self.assertIn("down --volumes --remove-orphans", calls[-1])
            self.assertFalse((root / ".env").exists())

    def test_unsafe_explicit_project_is_rejected_before_docker_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, bin_dir = self._fixture(directory)

            result = self._run(root, bin_dir, project_name="bumpabestie")

            self.assertEqual(result.returncode, 2)
            self.assertIn("must start with bumpabestie-smoke-", result.stderr)
            self.assertNotIn("PROJECT=", result.stdout)
            self.assertFalse((root / ".env").exists())

    def test_explicit_isolated_project_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, bin_dir = self._fixture(directory)

            result = self._run(root, bin_dir, project_name="bumpabestie-smoke-ci")

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [line for line in result.stdout.splitlines() if line.startswith("PROJECT=")]
            self.assertTrue(calls)
            self.assertTrue(all(line.startswith("PROJECT=bumpabestie-smoke-ci ") for line in calls))
            self.assertIn("down --volumes --remove-orphans", calls[-1])


if __name__ == "__main__":
    unittest.main()
