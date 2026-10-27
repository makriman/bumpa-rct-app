"""Build and test only owned, labelled local canary images; never deploy.

Run after the parent reviewer freezes infra/hermes source. Run with --full to
also build the unchanged release Dockerfile and execute the same harness there.
All Docker operations target the local desktop-linux context explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARTIFACTS = HERE / "artifacts"
CONTEXT = ARTIFACTS / "context"
LABEL = "com.bumpa.canary=hermes-context-20261027"
MINIMAL = "bumpa-hermes-context-canary:20261027"
RELEASE = "bumpa-hermes-context-release-canary:20261027"
BASE = "nousresearch/hermes-agent@sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args: list[str], name: str, timeout: int = 600, stdin=None) -> str:
    argv = ["docker", "--context", "desktop-linux", *args]
    print("Running:", " ".join(argv), flush=True)
    with (ARTIFACTS / (name + ".log")).open("w") as log:
        log.write("COMMAND=" + json.dumps(argv) + "\n")
        log.flush()
        process = subprocess.Popen(argv, stdin=stdin, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            status = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=30)
            raise RuntimeError(f"{name} exceeded {timeout}s; see its log")
    output = (ARTIFACTS / (name + ".log")).read_text()
    if status:
        print(output[-10000:], file=sys.stderr)
        raise RuntimeError(f"{name} exited {status}")
    return output


def inspect_image(image: str, name: str) -> dict:
    output = command(["image", "inspect", image], name)
    return json.loads(output.split("\n", 1)[1])[0]


def cleanup_owned_container(name: str, kind: str) -> None:
    argv = ["docker", "--context", "desktop-linux"]
    found = subprocess.run(
        argv + ["container", "inspect", name, "--format", '{{ index .Config.Labels "com.bumpa.canary" }}'],
        text=True, capture_output=True,
    )
    if found.returncode == 0:
        assert found.stdout.strip() == LABEL.split("=", 1)[1], "refusing to remove a container without the exact owned label"
        removed = subprocess.run(argv + ["container", "rm", "--force", "--volumes", name], text=True, capture_output=True, check=True)
        (ARTIFACTS / (kind + "-cleanup.log")).write_text(removed.stdout)
    else:
        assert "No such container" in found.stderr or "No such object" in found.stderr, found.stderr
        (ARTIFACTS / (kind + "-cleanup.log")).write_text("Confirmed disposable container already removed.\n")


def run(image: str, kind: str) -> dict:
    name = f"bumpa-hermes-context-canary-{kind}"
    # --rm cleans only this container. The label records its ownership. No host
    # ports, Docker socket, credentials, production volumes, or external network.
    args = [
        "run", "--rm", "--pull", "never", "--platform", "linux/amd64",
        "--name", name, "--label", LABEL, "--network", "none",
        "--memory", "2g", "--cpus", "2", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--env", "CANARY_EXPECT_NATIVE_DISABLED=1",
        "--env", f"CANARY_IMAGE_KIND={kind}",
    ]
    if kind == "release":
        args += ["--env", "CANARY_EXPECT_PRODUCTION_BASELINE=1"]
        # Read-only mount contains only the snapshotted synthetic harness and
        # public overlay source, never app config or production data.
        args += ["--mount", f"type=bind,source={CONTEXT / 'tests/hermes-canary'},target=/canary,readonly"]
    args += ["--entrypoint", "/opt/hermes/.venv/bin/python", image, "/canary/canary.py"]
    try:
        output = command(args, kind + "-run", timeout=240)
    finally:
        cleanup_owned_container(name, kind)
    results = [line.removeprefix("CANARY_RESULT=") for line in output.splitlines() if line.startswith("CANARY_RESULT=")]
    assert len(results) == 1, "successful canary result missing; inspect run log"
    result = json.loads(results[0])
    assert result["passed"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    ARTIFACTS.mkdir(exist_ok=True)
    CONTEXT.mkdir(exist_ok=True)
    snapshot_paths = [
        path for directory in [ROOT / "infra/hermes", ROOT / "infra/brotli"]
        for path in directory.rglob("*") if path.is_file()
    ] + [HERE / "Dockerfile", HERE / "canary.py"]
    hashes = {}
    for source in snapshot_paths:
        relative = source.relative_to(ROOT)
        target = CONTEXT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[str(relative)] = sha(source)
    # The full release image gets only this directory read-only at runtime.
    shutil.copy2(ROOT / "infra/hermes/apply_context_overlay.py", CONTEXT / "tests/hermes-canary/apply_context_overlay.py")
    (ARTIFACTS / "source-hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
    metadata = {"base": BASE, "source_hashes": hashes, "started_at_unix": time.time()}
    metadata["base_image"] = inspect_image(BASE, "base-image-inspect")["Id"]
    command([
        "build", "--platform", "linux/amd64", "--pull=false", "--network", "none",
        "--label", LABEL, "--progress", "plain", "--tag", MINIMAL,
        "--iidfile", str(ARTIFACTS / "minimal-image.id"),
        "--file", str(CONTEXT / "tests/hermes-canary/Dockerfile"), str(CONTEXT),
    ], "minimal-build")
    minimal_info = inspect_image(MINIMAL, "minimal-image-inspect")
    metadata["minimal_image"] = minimal_info["Id"]
    metadata["minimal_repo_digests"] = minimal_info.get("RepoDigests", [])
    metadata["minimal_result"] = run(MINIMAL, "minimal")
    if args.full:
        command([
            "build", "--platform", "linux/amd64", "--pull=false", "--network", "default",
            "--label", LABEL, "--progress", "plain", "--tag", RELEASE,
            "--iidfile", str(ARTIFACTS / "release-image.id"), "--target", "gateway-runtime",
            "--file", str(CONTEXT / "infra/hermes/Dockerfile"), str(CONTEXT),
        ], "release-build", timeout=1200)
        release_info = inspect_image(RELEASE, "release-image-inspect")
        metadata["release_image"] = release_info["Id"]
        metadata["release_repo_digests"] = release_info.get("RepoDigests", [])
        metadata["release_result"] = run(RELEASE, "release")
    changed = [relative for relative, before in hashes.items() if sha(ROOT / relative) != before]
    assert not changed, {"source_changed_during_canary": changed}
    metadata["source_unchanged_during_canary"] = True
    metadata["completed_at_unix"] = time.time()
    (ARTIFACTS / "result.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": True, "images": {key: value for key, value in metadata.items() if key.endswith("_image")}, "evidence": str(ARTIFACTS / "result.json")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
