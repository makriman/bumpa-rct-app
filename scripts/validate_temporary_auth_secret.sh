#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 3 )); then
  echo "Usage: $0 <auth-login-mode> <verifier-host-path> <exact-api-image>" >&2
  exit 2
fi

auth_login_mode="$1"
verifier_path="$2"
api_image="$3"
if [[ "$auth_login_mode" != "temporary_static_pin" ]]; then
  exit 0
fi
if [[ ! "$verifier_path" =~ ^/var/lib/[A-Za-z0-9._-]+/temporary_web_pin_verifier$ ]]; then
  echo "Temporary web PIN verifier host path is invalid" >&2
  exit 2
fi
if [[ ! "$api_image" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$ ]]; then
  echo "Temporary web PIN validation requires an immutable API image" >&2
  exit 2
fi

verifier_dir="${verifier_path%/*}"
if [[ ! -d "$verifier_dir" || -L "$verifier_dir" ]]; then
  echo "Temporary web PIN verifier directory is missing or unsafe" >&2
  exit 2
fi
verifier_dir_permissions="$(stat -c '%a' "$verifier_dir" 2>/dev/null || stat -f '%Lp' "$verifier_dir")"
verifier_dir_owner="$(stat -c '%u:%g' "$verifier_dir" 2>/dev/null || stat -f '%u:%g' "$verifier_dir")"
if [[ "$verifier_dir_permissions" != "700" || "$verifier_dir_owner" != "0:0" ]]; then
  echo "Temporary web PIN verifier directory must be root-owned with mode 0700" >&2
  exit 2
fi

if ! docker image inspect "$api_image" >/dev/null 2>&1; then
  echo "The exact API image must be pulled before temporary web PIN validation" >&2
  exit 2
fi
if ! docker run --rm --pull never \
  --network none \
  --read-only \
  --user 0:0 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,source=$verifier_dir,target=/run/temporary-auth-secret,readonly" \
  --entrypoint sh \
  "$api_image" -eu -c '
    verifier=/run/temporary-auth-secret/temporary_web_pin_verifier
    test "$(stat -c %u:%g /run/temporary-auth-secret)" = 0:0
    test "$(stat -c %a /run/temporary-auth-secret)" = 700
    test -f "$verifier"
    test ! -L "$verifier"
    test "$(stat -c %u:%g "$verifier")" = 0:0
    test "$(stat -c %a "$verifier")" = 600
    test "$(find /run/temporary-auth-secret -mindepth 1 -maxdepth 1 | wc -l)" = 1
    python3 -c '\''
import re
import sys

with open(sys.argv[1], "rb") as verifier_file:
    verifier_bytes = verifier_file.read()
raise SystemExit(0 if re.fullmatch(rb"[0-9a-f]{64}\x0a", verifier_bytes) else 1)
'\'' "$verifier"
  ' >/dev/null 2>&1; then
  echo "Temporary web PIN verifier failed isolated validation" >&2
  exit 2
fi
