"""Run the bounded offline speech smoke test in the verified Hermes image."""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path

from run import ARTIFACTS, LABEL, RELEASE, cleanup_owned_container, command, inspect_image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decoded-pcm-array", action="store_true")
    parser.add_argument("--image", default=RELEASE)
    parser.add_argument("--output-stem")
    args = parser.parse_args()
    script = Path(__file__).with_name("speech_canary.py")
    info = inspect_image(args.image, "speech-image-inspect")
    assert info["Config"]["Labels"]["io.bumpabestie.canonical-context-contract"] == "v1"
    container = "bumpa-hermes-speech-canary"
    mode = "decoded-pcm-array" if args.decoded_pcm_array else "file"
    stem = args.output_stem or ("speech-pcm-array" if args.decoded_pcm_array else "speech")
    with script.open() as source:
        try:
            output = command([
                "run", "--rm", "--interactive", "--pull", "never", "--platform", "linux/amd64",
                "--name", container, "--label", LABEL, "--network", "none",
                "--memory", "3g", "--cpus", "2", "--pids-limit", "256",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--env", "OMP_NUM_THREADS=2", "--env", "OPENBLAS_NUM_THREADS=2",
                "--env", "SPEECH_INPUT_MODE=" + mode,
                "--env", "MKL_NUM_THREADS=2", "--entrypoint", "/opt/hermes/.venv/bin/python",
                args.image, "-",
            ], stem + "-run", timeout=300, stdin=source)
        finally:
            cleanup_owned_container(container, stem)
    lines = [line.removeprefix("SPEECH_RESULT=") for line in output.splitlines() if line.startswith("SPEECH_RESULT=")]
    assert len(lines) == 1, "successful offline speech result missing"
    result = json.loads(lines[0])
    assert result["passed"]
    result["image"] = info["Id"]
    result["harness_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
    (ARTIFACTS / (stem + "-result.json")).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
