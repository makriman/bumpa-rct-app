from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).parents[2]
SMOKE_SCRIPT = ROOT_DIR / "scripts" / "smoke_test.sh"

FAKE_CURL = r"""#!/usr/bin/env bash
set -Eeuo pipefail

output=""
header_output=""
url=""
args=("$@")
for ((index = 0; index < ${#args[@]}; index++)); do
  case "${args[$index]}" in
    --output)
      output="${args[$((index + 1))]}"
      ;;
    --dump-header)
      header_output="${args[$((index + 1))]}"
      ;;
    http://* | https://*)
      url="${args[$index]}"
      ;;
  esac
done

call_number="$(grep -c '^CALL$' "$SMOKE_TEST_LOG" 2>/dev/null || true)"
nonce="fakeNonce${call_number}000000000000000000000000"

{
  printf 'CALL\n'
  printf 'ARG=%s\n' "$@"
  printf 'BODY=%s\n' "$output"
  printf 'HEADER=%s\n' "$header_output"
} >> "$SMOKE_TEST_LOG"
if [[ -n "$header_output" && "$url" == *bumpabestie.example.com/ \
  && "$url" != *www.* && "$url" != *admin.* && "$url" != *research.* ]]; then
  printf '<html><script nonce="%s"></script></html>\n' "$nonce" > "$output"
  printf '%s\r\n' \
    'HTTP/1.1 200 OK' \
    "Content-Security-Policy: default-src 'none'; script-src 'self' 'nonce-$nonce' 'strict-dynamic'; script-src-attr 'none'; style-src 'self' 'nonce-$nonce'; style-src-attr 'unsafe-inline';" \
    'Cache-Control: private, no-store' \
    '' > "$header_output"
else
  printf 'test response\n' > "$output"
fi
mode="$(stat -c '%a' "$output" 2>/dev/null || stat -f '%Lp' "$output")"
printf 'MODE=%s\n' "$mode" >> "$SMOKE_TEST_LOG"

if [[ "${SMOKE_TEST_CURL_MODE:-success}" == "failure" ]]; then
  printf '503'
elif [[ "$url" == *www.* ]]; then
  printf '308'
elif [[ "$url" == *admin.* || "$url" == *research.* ]]; then
  printf '307'
else
  printf '200'
fi
"""


class SmokeTestScriptTest(unittest.TestCase):
    def run_smoke(
        self,
        directory: str,
        *,
        origin_address: str | None = None,
        overall_timeout: str = "10",
        curl_mode: str = "success",
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        curl_path = bin_dir / "curl"
        curl_path.write_text(FAKE_CURL, encoding="utf-8")
        curl_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        log_path = root / "curl.log"
        environment = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(root),
            "SMOKE_SCHEME": "https",
            "SMOKE_PORT": "443",
            "SMOKE_OVERALL_TIMEOUT_SECONDS": overall_timeout,
            "SMOKE_TEST_CURL_MODE": curl_mode,
            "SMOKE_TEST_LOG": str(log_path),
            "APP_DOMAIN": "bumpabestie.example.com",
            "WWW_DOMAIN": "www.bumpabestie.example.com",
            "ADMIN_DOMAIN": "admin.bumpabestie.example.com",
            "RESEARCH_DOMAIN": "research.bumpabestie.example.com",
            "API_DOMAIN": "api.bumpabestie.example.com",
        }
        if origin_address is None:
            environment.pop("SMOKE_ORIGIN_ADDRESS", None)
        else:
            environment["SMOKE_ORIGIN_ADDRESS"] = origin_address
        result = subprocess.run(  # noqa: S603 - fixed repository script
            [str(SMOKE_SCRIPT)],
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result, log_path

    def test_origin_override_resolves_every_tls_hostname_without_disabling_tls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, log_path = self.run_smoke(directory, origin_address="127.0.0.1")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("PASS "), 7)
            log = log_path.read_text(encoding="utf-8")
            self.assertEqual(log.count("CALL\n"), 8)
            self.assertEqual(log.count("ARG=--noproxy\n"), 8)
            self.assertEqual(log.count("ARG=*\n"), 8)
            self.assertNotIn("ARG=--insecure\n", log)
            self.assertNotIn("ARG=-k\n", log)
            for host in (
                "bumpabestie.example.com",
                "www.bumpabestie.example.com",
                "admin.bumpabestie.example.com",
                "research.bumpabestie.example.com",
                "api.bumpabestie.example.com",
            ):
                self.assertIn(f"ARG={host}:443:127.0.0.1\n", log)
            self.assertEqual(log.count("MODE=600\n"), 8)
            body_paths = {
                Path(line.removeprefix("BODY="))
                for line in log.splitlines()
                if line.startswith("BODY=")
            }
            self.assertEqual(len(body_paths), 1)
            self.assertTrue(all(not path.exists() for path in body_paths))

    def test_edge_mode_does_not_override_dns_or_proxy_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, log_path = self.run_smoke(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            log = log_path.read_text(encoding="utf-8")
            self.assertNotIn("ARG=--resolve\n", log)
            self.assertNotIn("ARG=--noproxy\n", log)

    def test_timeout_must_be_a_positive_integer(self) -> None:
        for invalid_timeout in ("0", "-1", "1.5", "invalid"):
            with self.subTest(invalid_timeout=invalid_timeout):
                with tempfile.TemporaryDirectory() as directory:
                    result, log_path = self.run_smoke(
                        directory,
                        overall_timeout=invalid_timeout,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertIn("must be a positive integer", result.stderr)
                    self.assertFalse(log_path.exists())

    def test_positive_timeout_with_a_leading_zero_is_decimal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, _log_path = self.run_smoke(directory, overall_timeout="08")

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_retries_cannot_exceed_the_shared_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            started_at = time.monotonic()
            result, log_path = self.run_smoke(
                directory,
                overall_timeout="1",
                curl_mode="failure",
            )
            elapsed = time.monotonic() - started_at

            self.assertEqual(result.returncode, 1)
            self.assertLess(elapsed, 3)
            self.assertIn("FAIL API health", result.stderr)
            log = log_path.read_text(encoding="utf-8")
            self.assertEqual(log.count("CALL\n"), 1)
            self.assertIn("ARG=--max-time\nARG=1\n", log)


if __name__ == "__main__":
    unittest.main()
