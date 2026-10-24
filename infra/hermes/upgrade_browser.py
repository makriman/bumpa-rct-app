"""Install the checksummed browser overlay during the amd64 image build."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pm.build_operations import prepare_tools
from pm.install import installed_package
from pm.lock import Lockfile
from pm.paths import lockfile_path
from pm.store import current_target


def main() -> None:
    pin = json.loads(Path("/tmp/browser-runtime.json").read_text())
    if current_target() not in pin["artifacts"]:
        raise RuntimeError("No reviewed browser artifact for this image architecture")
    previous = installed_package("chromium")
    lock = Lockfile(lockfile_path())
    lock.set_pin("chromium", pin["version"], pin["artifacts"])
    lock.save()
    prepare_tools(["chromium"], out=Path("/opt/hermes/tools"), target=current_target())
    installed = installed_package("chromium")
    if installed is None or installed.version != pin["version"] or installed.binary is None:
        raise RuntimeError("The patched browser is not discoverable")
    if not installed.path.is_relative_to(Path("/opt/hermes/tools")):
        raise RuntimeError("The patched browser is not in the shared image")
    version = subprocess.check_output([str(installed.binary), "--version"], text=True)
    if pin["version"] not in version:
        raise RuntimeError("The browser binary does not match its recorded version")
    if previous is not None and previous.path != installed.path:
        previous.path.relative_to(Path("/opt/hermes/tools"))
        shutil.rmtree(previous.path)
    print(version.strip())


if __name__ == "__main__":
    main()
