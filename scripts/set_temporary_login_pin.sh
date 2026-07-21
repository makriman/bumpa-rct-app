#!/usr/bin/env bash
set -Eeuo pipefail

if ((EUID != 0)); then
  echo "Run this command as root so the production PIN remains root-owned" >&2
  exit 2
fi
if (($# > 1)); then
  echo "Usage: $0 [production-env-file]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Rotation and promotion both rewrite the production environment. They must
# never observe or commit overlapping auth boundaries.
source "$ROOT_DIR/scripts/maintenance_lock.sh"
acquire_maintenance_lock
source "$ROOT_DIR/scripts/promotion_state.sh"
assert_maintenance_clear
source "$ROOT_DIR/scripts/release_boundary.sh"

env_file="${1:-/opt/bumpabestie/.env.production}"
if [[ ! -f "$env_file" || -L "$env_file" ]]; then
  echo "Production environment file must be a regular non-symlink file" >&2
  exit 2
fi
env_mode="$(stat -c '%a' "$env_file" 2>/dev/null || stat -f '%Lp' "$env_file")"
if [[ "$env_mode" != "600" ]]; then
  echo "Production environment file must have mode 0600" >&2
  exit 2
fi
env_dir="$(cd "$(dirname "$env_file")" && pwd -P)"
env_file="$env_dir/$(basename "$env_file")"
release_file="$env_dir/.deployed-release.json"
if [[ ! -f "$release_file" || -L "$release_file" ]]; then
  echo "A private deployed release record is required before PIN staging" >&2
  exit 2
fi
release_mode="$(stat -c '%a' "$release_file" 2>/dev/null || stat -f '%Lp' "$release_file")"
if [[ "$release_mode" != "600" ]]; then
  echo "The deployed release record must have mode 0600" >&2
  exit 2
fi

env_value() {
  local key="$1" count
  count="$(awk -F= -v key="$key" '$1 == key {count++} END {print count+0}' "$env_file")"
  [[ "$count" == "1" ]] || return 1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$env_file"
}

for key in \
  AUTH_LOGIN_MODE OTP_SECRET TEMPORARY_WEB_PIN_VERIFIER \
  TEMPORARY_WEB_PIN_VERIFIER_FILE TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST \
  TEMPORARY_WEB_PIN_EXPIRES_AT WHATSAPP_BACKEND \
  META_PRIMARY_SENDER_ENABLED META_TEST_SENDER_VERIFICATION_MODE \
  PROACTIVE_INSIGHTS_ENABLED DAILY_INSIGHTS_ENABLED WEEKLY_INSIGHTS_ENABLED; do
  if ! env_value "$key" >/dev/null; then
    echo "Production authentication settings are incomplete or duplicated" >&2
    exit 2
  fi
done

whatsapp_backend="$(env_value WHATSAPP_BACKEND)"
meta_primary_sender_enabled="$(env_value META_PRIMARY_SENDER_ENABLED)"
meta_test_sender_mode="$(env_value META_TEST_SENDER_VERIFICATION_MODE)"
cadences_disabled=0
if [[ "$(env_value PROACTIVE_INSIGHTS_ENABLED)" == "false" \
  && "$(env_value DAILY_INSIGHTS_ENABLED)" == "false" \
  && "$(env_value WEEKLY_INSIGHTS_ENABLED)" == "false" ]]; then
  cadences_disabled=1
fi
whatsapp_boundary_safe=0
if [[ "$whatsapp_backend" == "disabled" \
  && "$meta_primary_sender_enabled" =~ ^(true|false)$ \
  && "$meta_test_sender_mode" == "disabled" \
  && "$cadences_disabled" == "1" ]]; then
  whatsapp_boundary_safe=1
elif [[ "$whatsapp_backend" == "meta" \
  && "$meta_primary_sender_enabled" == "false" \
  && "$meta_test_sender_mode" == "inbound_replies_only" \
  && "$cadences_disabled" == "1" ]]; then
  whatsapp_boundary_safe=1
fi

if [[ "$(env_value AUTH_LOGIN_MODE)" != "temporary_static_pin" \
  || -n "$(env_value TEMPORARY_WEB_PIN_VERIFIER)" \
  || "$(env_value TEMPORARY_WEB_PIN_VERIFIER_FILE)" != "/run/auth-secret/temporary_web_pin_verifier" \
  || "$whatsapp_boundary_safe" != "1" ]]; then
  echo "Stage the fail-closed temporary authentication selectors before setting a PIN" >&2
  exit 2
fi
if ! python3 - "$(env_value TEMPORARY_WEB_PIN_EXPIRES_AT)" <<'PY'
from datetime import datetime, timezone
import sys

try:
    expiry = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
except ValueError:
    raise SystemExit(1)
raise SystemExit(
    0
    if expiry.tzinfo is not None
    and expiry.astimezone(timezone.utc) > datetime.now(timezone.utc)
    else 1
)
PY
then
  echo "Temporary web PIN expiry must be a future timezone-aware timestamp" >&2
  exit 2
fi

if ! load_release_boundary "$release_file"; then
  echo "The deployed release record is invalid" >&2
  exit 2
fi
deployed_mode="$RELEASE_AUTH_LOGIN_MODE"
deployed_path="$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"
staged_path="$(env_value TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST)"

private_verifier_file_is_safe() {
  local path="$1" directory mode owner directory_mode directory_owner
  validate_temporary_verifier_host_path "$path" || return 1
  directory="${path%/*}"
  [[ -d "$directory" && ! -L "$directory" && -f "$path" && ! -L "$path" ]] || return 1
  directory_mode="$(stat -c '%a' "$directory" 2>/dev/null || stat -f '%Lp' "$directory")"
  directory_owner="$(stat -c '%u:%g' "$directory" 2>/dev/null || stat -f '%u:%g' "$directory")"
  mode="$(stat -c '%a' "$path" 2>/dev/null || stat -f '%Lp' "$path")"
  owner="$(stat -c '%u:%g' "$path" 2>/dev/null || stat -f '%u:%g' "$path")"
  [[ "$directory_mode" == "700" && "$directory_owner" == "0:0" \
    && "$mode" == "600" && "$owner" == "0:0" ]] || return 1
  python3 - "$path" <<'PY'
import re
import sys

with open(sys.argv[1], "rb") as verifier_file:
    verifier = verifier_file.read()
raise SystemExit(0 if re.fullmatch(rb"[0-9a-f]{64}\n", verifier) else 1)
PY
}

if [[ "$deployed_mode" == "temporary_static_pin" ]]; then
  if [[ "$staged_path" != "$deployed_path" ]]; then
    echo "A different PIN rotation is already staged; promote or discard it first" >&2
    exit 2
  fi
  if ! private_verifier_file_is_safe "$deployed_path"; then
    echo "The deployed rollback verifier is missing or unsafe" >&2
    exit 2
  fi
elif [[ -n "$staged_path" ]]; then
  echo "The first temporary PIN activation must start with a blank host verifier path" >&2
  exit 2
fi

otp_secret="$(env_value OTP_SECRET)"
if [[ ${#otp_secret} -lt 24 ]]; then
  unset otp_secret
  echo "Production OTP_SECRET is unavailable or invalid" >&2
  exit 2
fi
IFS= read -r -s -p "Enter the six-digit temporary web login PIN: " pin </dev/tty
printf '\n' >/dev/tty
if [[ ! "$pin" =~ ^[0-9]{6}$ ]]; then
  unset pin otp_secret
  echo "PIN must contain exactly six digits" >&2
  exit 2
fi

verifier_root="/var/lib/bumpabestie-auth-secret"
verifier_versions="$verifier_root/temporary-web-pin-verifiers"
ensure_private_directory() {
  local directory="$1" mode owner created=0
  if [[ ! -e "$directory" ]]; then
    mkdir -m 0700 "$directory"
    created=1
  fi
  [[ -d "$directory" && ! -L "$directory" ]] || return 1
  mode="$(stat -c '%a' "$directory" 2>/dev/null || stat -f '%Lp' "$directory")"
  owner="$(stat -c '%u:%g' "$directory" 2>/dev/null || stat -f '%u:%g' "$directory")"
  [[ "$mode" == "700" && "$owner" == "0:0" ]] || return 1
  if ((created)); then
    sync -f "${directory%/*}"
  fi
}
if ! ensure_private_directory "$verifier_root" \
  || ! ensure_private_directory "$verifier_versions"; then
  unset pin otp_secret
  echo "The immutable verifier directory is missing or unsafe" >&2
  exit 2
fi

generated_path=""
env_candidate=""
retain_generated=0
cleanup() {
  rm -f -- "${env_candidate:-}"
  if ((retain_generated == 0)) && [[ -n "${generated_path:-}" ]]; then
    rm -f -- "$generated_path"
    sync -f "$verifier_versions" 2>/dev/null || true
  fi
  unset pin otp_secret
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# The PIN and OTP secret cross stdin only. The generated filename is a random,
# non-secret identifier; O_EXCL and O_NOFOLLOW make every version immutable.
generated_path="$(python3 - "$verifier_versions" \
  3< <({ printf '%s\n' "$otp_secret"; printf '%s\n' "$pin"; }) <<'PY'
import hashlib
import hmac
import os
import secrets
import sys

directory = sys.argv[1]
with os.fdopen(3, "r", encoding="utf-8") as secret_input:
    secret = secret_input.readline().rstrip("\n")
    pin = secret_input.readline().rstrip("\n")
payload = hmac.new(
    secret.encode(), f"web-login-pin:{pin}".encode(), hashlib.sha256
).hexdigest().encode() + b"\n"
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
for _ in range(128):
    candidate = os.path.join(directory, secrets.token_hex(16))
    try:
        descriptor = os.open(candidate, flags, 0o600)
    except FileExistsError:
        continue
    with os.fdopen(descriptor, "wb") as verifier_file:
        os.fchmod(verifier_file.fileno(), 0o600)
        os.fchown(verifier_file.fileno(), 0, 0)
        verifier_file.write(payload)
        verifier_file.flush()
        os.fsync(verifier_file.fileno())
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print(candidate)
    break
else:
    raise SystemExit("unable to allocate a unique verifier version")
PY
)"
unset pin otp_secret
if ! temporary_verifier_host_path_is_versioned "$generated_path" \
  || ! private_verifier_file_is_safe "$generated_path"; then
  echo "The new verifier version failed private-file validation" >&2
  exit 2
fi

env_owner="$(stat -c '%u:%g' "$env_file" 2>/dev/null || stat -f '%u:%g' "$env_file")"
env_candidate="$(mktemp "$env_dir/.env.production.pin-rotation.XXXXXX")"
chmod 0600 "$env_candidate"
if ! awk -F= -v path="$generated_path" '
  $1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" {
    if (++seen > 1) exit 42
    print "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=" path
    next
  }
  { print }
  END { if (seen != 1) exit 43 }
' "$env_file" >"$env_candidate"; then
  echo "Unable to stage the new verifier path in the production environment" >&2
  exit 2
fi
chown "$env_owner" "$env_candidate"
chmod 0600 "$env_candidate"
sync -f "$env_candidate"

# From this point an interrupted rename may leave an unreferenced private file,
# but can never leave the environment pointing at a deleted verifier.
retain_generated=1
if ! mv -f "$env_candidate" "$env_file"; then
  retain_generated=0
  echo "Unable to atomically stage the new verifier path" >&2
  exit 70
fi
env_candidate=""
sync -f "$env_dir"
trap - EXIT HUP INT TERM
echo "A new immutable temporary web PIN verifier was staged without displaying any credential"
