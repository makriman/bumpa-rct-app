#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 2 )); then
  echo "Usage: $0 <auth-login-mode> <secrets-directory>" >&2
  exit 2
fi

auth_login_mode="$1"
secrets_dir="$2"
if [[ "$auth_login_mode" != "temporary_static_pin" ]]; then
  exit 0
fi

verifier_path="$secrets_dir/temporary_web_pin_verifier"
if [[ ! -f "$verifier_path" || -L "$verifier_path" ]] || \
  ! grep -Eq '^[0-9a-f]{64}$' "$verifier_path" || \
  [[ "$(wc -l < "$verifier_path")" -gt 1 ]]; then
  echo "Temporary web PIN verifier must be a lowercase SHA-256 HMAC" >&2
  exit 2
fi

verifier_permissions="$(stat -c '%a' "$verifier_path" 2>/dev/null || stat -f '%Lp' "$verifier_path")"
if [[ "$verifier_permissions" != "600" ]]; then
  echo "Production secret file permissions must be 0600: temporary_web_pin_verifier" >&2
  exit 2
fi
