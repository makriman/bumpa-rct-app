#!/usr/bin/env sh
set -eu

source_path="${TEMPORARY_WEB_PIN_VERIFIER_SOURCE:-/run/secrets/temporary_web_pin_verifier}"
target_dir="${AUTH_SECRET_TARGET_DIR:-/runtime-auth-secret}"
target_path="$target_dir/temporary_web_pin_verifier"
temporary_path="$target_dir/.temporary_web_pin_verifier.tmp"
runtime_uid="${RUNTIME_SECRET_UID:-100}"
runtime_gid="${RUNTIME_SECRET_GID:-101}"
auth_login_mode="${AUTH_LOGIN_MODE:-disabled}"

umask 077
mkdir -p "$target_dir"
rm -f "$temporary_path"

case "$auth_login_mode" in
  disabled | whatsapp_otp)
    find "$target_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    chown "$runtime_uid:$runtime_gid" "$target_dir"
    chmod 0500 "$target_dir"
    exit 0
    ;;
  temporary_static_pin) ;;
  *)
    echo "AUTH_LOGIN_MODE is invalid for auth-secret initialization" >&2
    exit 1
    ;;
esac

if [ ! -f "$source_path" ] || [ -L "$source_path" ]; then
  echo "Temporary web PIN verifier secret is missing or invalid" >&2
  exit 1
fi
if ! python3 -c '
import re
import sys

with open(sys.argv[1], "rb") as verifier_file:
    verifier = verifier_file.read()
raise SystemExit(0 if re.fullmatch(rb"[0-9a-f]{64}\x0a", verifier) else 1)
' "$source_path"; then
  echo "Temporary web PIN verifier must be a lowercase SHA-256 HMAC" >&2
  exit 1
fi

cp "$source_path" "$temporary_path"
chown "$runtime_uid:$runtime_gid" "$temporary_path"
chmod 0400 "$temporary_path"
mv -f "$temporary_path" "$target_path"
find "$target_dir" -mindepth 1 -maxdepth 1 -type f ! -name temporary_web_pin_verifier -delete
chown "$runtime_uid:$runtime_gid" "$target_dir"
chmod 0500 "$target_dir"
