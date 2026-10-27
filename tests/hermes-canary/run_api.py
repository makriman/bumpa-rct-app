"""Build and inspect the API only AFTER the coordinator explicitly freezes it.

Preparing this script does not freeze app source. Execute only after notification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from run import ARTIFACTS, HERE, LABEL, ROOT, cleanup_owned_container, command, inspect_image, sha


IMAGE = "bumpa-api-context-release-canary:20261027"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-frozen", action="store_true", required=True)
    parser.parse_args()
    context = ARTIFACTS / "api-context"
    if context.exists():
        shutil.rmtree(context)
    context.mkdir(parents=True, exist_ok=True)
    sources = [
        ROOT / name for name in [
            "apps/api/Dockerfile", "apps/api/pyproject.toml", "apps/api/uv.lock", "apps/api/alembic.ini",
            "scripts/init_runtime_secrets.sh", "scripts/init_auth_secret.sh", "contracts/openapi.json",
        ]
    ]
    for directory in [ROOT / "apps/api/app", ROOT / "apps/api/alembic"]:
        sources += [path for path in directory.rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path.name != ".DS_Store"]
    hashes = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        assert source.suffix not in {".env", ".pem", ".key", ".p12"}, relative
        target = context / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[str(relative)] = sha(source)
    runtime_harness = context / "canary"
    runtime_harness.mkdir(exist_ok=True)
    shutil.copy2(HERE / "api_canary.py", runtime_harness / "api_canary.py")
    shutil.copy2(ROOT / "contracts/openapi.json", runtime_harness / "openapi.json")
    (runtime_harness / "api-source-hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
    command([
        "build", "--platform", "linux/amd64", "--pull=false", "--network", "default",
        "--label", LABEL, "--progress", "plain", "--tag", IMAGE,
        "--iidfile", str(ARTIFACTS / "api-image.id"),
        "--file", str(context / "apps/api/Dockerfile"), str(context),
    ], "api-build", timeout=900)
    info = inspect_image(IMAGE, "api-image-inspect")
    assert info["Config"]["Labels"]["io.bumpabestie.canonical-context-contract"] == "v1"
    assert info["Config"]["User"] == "app", info["Config"]["User"]
    container = "bumpa-api-context-canary"
    program = (HERE / "api_canary.py").read_text().replace(
        "from __future__ import annotations\n",
        "from __future__ import annotations\n\nCANARY_MANIFEST = " + repr(hashes)
        + "\nCANARY_CONTRACT = " + repr((ROOT / "contracts/openapi.json").read_text()) + "\n",
        1,
    )
    payload = context / "api_canary_payload.py"
    payload.write_text(program)
    try:
        with payload.open() as source:
            output = command([
                "run", "--rm", "--interactive", "--pull", "never", "--platform", "linux/amd64",
                "--name", container, "--label", LABEL, "--network", "none",
                "--memory", "1g", "--cpus", "2", "--pids-limit", "128",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--entrypoint", "/app/.venv/bin/python", IMAGE, "-",
            ], "api-run", timeout=180, stdin=source)
    finally:
        cleanup_owned_container(container, "api")
    lines = [line.removeprefix("API_CANARY_RESULT=") for line in output.splitlines() if line.startswith("API_CANARY_RESULT=")]
    assert len(lines) == 1, "API canary result missing"
    result = json.loads(lines[0])
    assert result["passed"]
    assert result["effective_user_id"] != 0
    assert all(sha(ROOT / relative) == before for relative, before in hashes.items()), "API source changed during canary"
    result.update({"image": info["Id"], "repo_digests": info.get("RepoDigests", []), "source_hashes": hashes, "source_unchanged_during_canary": True})
    (ARTIFACTS / "api-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": True, "image": info["Id"], "source_files_checked": result["actual_image_source_hashes_checked"]}, indent=2))


if __name__ == "__main__":
    main()
