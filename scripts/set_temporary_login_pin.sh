#!/usr/bin/env bash
set -Eeuo pipefail

if (( EUID != 0 )); then
  echo "Run this command as root so the production PIN remains root-owned" >&2
  exit 2
fi
if (( $# > 1 )); then
  echo "Usage: $0 [production-env-file]" >&2
  exit 2
fi

env_file="${1:-/opt/bumpabestie/.env.production}"
if [[ ! -f "$env_file" || -L "$env_file" ]]; then
  echo "Production environment file must be a regular non-symlink file" >&2
  exit 2
fi
verifier_path="$(awk -F= '$1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" {sub(/^[^=]*=/, ""); print; exit}' "$env_file")"
if ! python3 - "$verifier_path" <<'PY'
import os
import re
import sys

path = sys.argv[1]
if (
    not re.fullmatch(r"/var/lib/[A-Za-z0-9._-]+/temporary_web_pin_verifier", path)
    or os.path.normpath(path) != path
):
    raise SystemExit(1)
PY
then
  echo "Production verifier path is unavailable or invalid" >&2
  exit 2
fi
verifier_dir="${verifier_path%/*}"
if [[ -L "$verifier_dir" || -L "$verifier_path" ]]; then
  echo "Production verifier path must not contain a final symlink" >&2
  exit 2
fi

install -d -o root -g root -m 0700 "$verifier_dir"
otp_secret="$(awk -F= '$1 == "OTP_SECRET" {sub(/^[^=]*=/, ""); print; exit}' "$env_file")"
if [[ ${#otp_secret} -lt 24 ]]; then
  unset otp_secret
  echo "Production OTP_SECRET is unavailable or invalid" >&2
  exit 2
fi
IFS= read -r -s -p "Enter the six-digit temporary web login PIN: " pin </dev/tty
printf '\n' >/dev/tty
if [[ ! "$pin" =~ ^[0-9]{6}$ ]]; then
  unset pin
  echo "PIN must contain exactly six digits" >&2
  exit 2
fi

temporary_path="$(mktemp "$verifier_dir/.temporary_web_pin_verifier.XXXXXX")"
cleanup() {
  rm -f "$temporary_path"
  unset pin otp_secret
}
trap cleanup EXIT
verifier="$({ printf '%s\n' "$otp_secret"; printf '%s\n' "$pin"; } | python3 -c '
import hashlib
import hmac
import sys

secret = sys.stdin.readline().rstrip("\n")
pin = sys.stdin.readline().rstrip("\n")
sys.stdout.write(hmac.new(secret.encode(), f"web-login-pin:{pin}".encode(), hashlib.sha256).hexdigest())
')"
unset pin otp_secret
printf '%s\n' "$verifier" > "$temporary_path"
unset verifier
chown root:root "$temporary_path"
chmod 0600 "$temporary_path"
mv -f "$temporary_path" "$verifier_path"
sync -f "$verifier_dir"
trap - EXIT
echo "Temporary web login PIN installed without displaying its value"
