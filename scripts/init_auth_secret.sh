#!/usr/bin/env sh
set -eu

source_path="${TEMPORARY_WEB_PIN_VERIFIER_SOURCE:-/run/secrets/temporary_web_pin_verifier}"
target_dir="${AUTH_SECRET_TARGET_DIR:-/runtime-auth-secret}"
target_path="$target_dir/temporary_web_pin_verifier"
temporary_path="$target_dir/.temporary_web_pin_verifier.tmp"
runtime_uid="${RUNTIME_SECRET_UID:-100}"
runtime_gid="${RUNTIME_SECRET_GID:-101}"

if [ ! -f "$source_path" ] || [ -L "$source_path" ]; then
  echo "Temporary web PIN verifier secret is missing or invalid" >&2
  exit 1
fi
if ! grep -Eq '^[0-9a-f]{64}$' "$source_path" || [ "$(wc -l < "$source_path")" -gt 1 ]; then
  echo "Temporary web PIN verifier must be a lowercase SHA-256 HMAC" >&2
  exit 1
fi

umask 077
mkdir -p "$target_dir"
rm -f "$temporary_path"
cp "$source_path" "$temporary_path"
chown "$runtime_uid:$runtime_gid" "$temporary_path"
chmod 0400 "$temporary_path"
mv -f "$temporary_path" "$target_path"
find "$target_dir" -mindepth 1 -maxdepth 1 -type f ! -name temporary_web_pin_verifier -delete
chown "$runtime_uid:$runtime_gid" "$target_dir"
chmod 0500 "$target_dir"
