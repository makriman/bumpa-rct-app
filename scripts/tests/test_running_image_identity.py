import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "scripts/deploy.sh").read_text()
MATCH = re.search(r"(?ms)^running_image\(\) \{.*?^\}\n", SOURCE)
assert MATCH is not None
FUNCTION = MATCH.group()
IMAGE = "sha256:" + "a" * 64
REF = "ghcr.io/example/bumpabestie-api@sha256:" + "b" * 64
LOCAL = "bumpabestie-api@sha256:" + "c" * 64


class RunningImageIdentityTests(unittest.TestCase):
    def resolve(self, recorded: str, alias_id: str, actual_id: str = IMAGE) -> str:
        harness = r"""
set -Eeuo pipefail
compose_stub() { printf 'synthetic-container\n'; }
compose=(compose_stub)
docker() {
  if [[ "$1" == inspect && "$3" == '{{.Config.Image}}' ]]; then
    printf 'bumpabestie-api:security-synthetic\n'
  elif [[ "$1" == inspect && "$3" == '{{.Image}}' ]]; then
    printf '%s\n' "$ACTUAL_IMAGE"
  elif [[ "$1" == image && "$2" == inspect && "$4" == '{{.Id}}' ]]; then
    printf '%s\n' "$ALIAS_IMAGE"
  elif [[ "$1" == image && "$2" == inspect && "$4" == '{{json .RepoDigests}}' ]]; then
    printf '["%s"]\n' "$LOCAL_REFERENCE"
  else
    printf 'Unexpected Docker operation\n' >&2
    return 1
  fi
}
"""
        result = subprocess.run(
            ["/bin/bash"],
            input=harness + FUNCTION + '\nrunning_image api "$RECORDED_REFERENCE"',
            env={
                **os.environ,
                "ACTUAL_IMAGE": actual_id,
                "ALIAS_IMAGE": alias_id,
                "LOCAL_REFERENCE": LOCAL,
                "RECORDED_REFERENCE": recorded,
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def test_published_digest_alias_accepts_only_identical_running_image(self) -> None:
        self.assertEqual(self.resolve(REF, IMAGE), REF)

    def test_stale_record_cannot_replace_the_observed_running_image(self) -> None:
        observed = self.resolve(REF, "sha256:" + "d" * 64)
        self.assertEqual(observed, LOCAL)
        self.assertNotEqual(observed, REF)

    def test_mutable_alias_is_never_accepted_even_for_identical_content(self) -> None:
        self.assertEqual(self.resolve("ghcr.io/example/bumpabestie-api:latest", IMAGE), LOCAL)

    def test_unrecorded_baseline_preserves_original_digest_resolution(self) -> None:
        self.assertEqual(self.resolve("", IMAGE), LOCAL)

    def test_missing_running_identity_fails_closed(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            self.resolve(REF, "", actual_id="")
