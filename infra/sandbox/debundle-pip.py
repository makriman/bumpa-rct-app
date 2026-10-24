"""Apply pip's documented downstream debundling method to pinned wheel files.

https://pip.pypa.io/en/stable/development/vendoring-policy/#debundling
The complete wheel set lives beside __init__.py; no global imports are required.
"""

from pathlib import Path
import shutil
import pip

vendor = Path(pip.__file__).parent / "_vendor"
for entry in vendor.iterdir():
    if entry.name in {"__init__.py", "vendor.txt"} or entry.suffix == ".whl":
        continue
    if entry.is_dir():
        shutil.rmtree(entry)
    else:
        entry.unlink()
init = vendor / "__init__.py"
source = init.read_text()
assert source.count("DEBUNDLED = False") == 1
init.write_text(source.replace("DEBUNDLED = False", "DEBUNDLED = True"))
# Describe actual installed dependencies, including the security updates.
(vendor / "vendor.txt").write_text(Path("/tmp/pip-dependencies.in").read_text())
for installer in vendor.parent.parent.glob("pip-*.dist-info/INSTALLER"):
    installer.write_text("bumpabestie-image\n")
