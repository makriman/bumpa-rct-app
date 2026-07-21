#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${BUMPABESTIE_PIN_ROTATION_CONTRACT_CONTAINER:-0}" != "1" ]]; then
  command -v docker >/dev/null 2>&1 || {
    echo "Temporary PIN rotation contract requires Docker" >&2
    exit 2
  }
  image_id="$(docker build --quiet - \
    <"$ROOT_DIR/infra/tests/pin-rotation.Dockerfile")"
  docker run --rm --network none \
    --env BUMPABESTIE_PIN_ROTATION_CONTRACT_CONTAINER=1 \
    --volume "$ROOT_DIR/scripts/test_temporary_login_pin_rotation.sh:/workspace/scripts/test_temporary_login_pin_rotation.sh:ro" \
    --volume "$ROOT_DIR/scripts/set_temporary_login_pin.sh:/workspace/scripts/set_temporary_login_pin.sh:ro" \
    --volume "$ROOT_DIR/scripts/maintenance_lock.sh:/workspace/scripts/maintenance_lock.sh:ro" \
    --volume "$ROOT_DIR/scripts/promotion_state.sh:/workspace/scripts/promotion_state.sh:ro" \
    --volume "$ROOT_DIR/scripts/release_boundary.sh:/workspace/scripts/release_boundary.sh:ro" \
    "$image_id" /workspace/scripts/test_temporary_login_pin_rotation.sh
  exit
fi

test "$(id -u)" = 0
setter=/workspace/scripts/set_temporary_login_pin.sh
# shellcheck source=scripts/release_boundary.sh
source /workspace/scripts/release_boundary.sh

install -d -m 0700 /var/lib/bumpabestie /opt/bumpabestie
env_file=/opt/bumpabestie/.env.production
release_file=/opt/bumpabestie/.deployed-release.json
revision=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
image=registry.example/bumpabestie/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
runtime_path=/run/auth-secret/temporary_web_pin_verifier
future_expiry=2099-01-01T00:00:00Z
otp_secret="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"

write_env() {
  local mode="$1" host_path="$2" whatsapp_backend="${3:-disabled}"
  local meta_primary_sender_enabled=true meta_test_sender_mode=disabled
  if [[ "$whatsapp_backend" == "meta" ]]; then
    meta_primary_sender_enabled=false
    meta_test_sender_mode=inbound_replies_only
  fi
  printf '%s\n' \
    "DEPLOY_REF=$revision" \
    "IMAGE_TAG=sha-$revision" \
    "INFRA_IMAGE_TAG=sha-$revision" \
    "API_IMAGE=$image" "WEB_IMAGE=$image" "CADDY_IMAGE=$image" \
    "POSTGRES_IMAGE=$image" "BACKUP_IMAGE=$image" "HERMES_IMAGE=$image" \
    "OTP_SECRET=$otp_secret" \
    "AUTH_LOGIN_MODE=$mode" \
    'TEMPORARY_WEB_PIN_VERIFIER=' \
    "TEMPORARY_WEB_PIN_VERIFIER_FILE=$([[ "$mode" == temporary_static_pin ]] && printf %s "$runtime_path")" \
    "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=$host_path" \
    "TEMPORARY_WEB_PIN_EXPIRES_AT=$([[ "$mode" == temporary_static_pin ]] && printf %s "$future_expiry")" \
    "WHATSAPP_BACKEND=$whatsapp_backend" \
    "META_PRIMARY_SENDER_ENABLED=$meta_primary_sender_enabled" \
    "META_TEST_SENDER_VERIFICATION_MODE=$meta_test_sender_mode" \
    'PROACTIVE_INSIGHTS_ENABLED=false' \
    'DAILY_INSIGHTS_ENABLED=false' \
    'WEEKLY_INSIGHTS_ENABLED=false' >"$env_file"
  chmod 0600 "$env_file"
}

write_release() {
  local mode="$1" host_path="$2" whatsapp_backend="${3:-disabled}"
  jq --null-input \
    --arg revision "$revision" --arg image "$image" --arg mode "$mode" \
    --arg whatsapp_backend "$whatsapp_backend" \
    --arg runtime "$([[ "$mode" == temporary_static_pin ]] && printf %s "$runtime_path")" \
    --arg host "$host_path" \
    --arg expiry "$([[ "$mode" == temporary_static_pin ]] && printf %s "$future_expiry")" '
    {
      revision: $revision, operations_revision: $revision,
      image_tag: ("sha-" + $revision), infra_image_tag: ("sha-" + $revision),
      images: {
        api: $image, worker: $image, scheduler: $image, web: $image,
        caddy: $image, postgres: $image,
        redis: ("redis@" + ($image | split("@")[1])),
        backup: $image, hermes: $image
      },
      auth: {
        login_mode: $mode,
        temporary_web_pin_verifier_file: $runtime,
        temporary_web_pin_verifier_file_host: $host,
        temporary_web_pin_expires_at: $expiry,
        whatsapp_backend: $whatsapp_backend
      }
    }
  ' >"$release_file"
  chmod 0600 "$release_file"
}

run_setter_with_private_random_pin() {
  local fail_before_rename="${1:-0}"
  local setter_path="$PATH"
  if [[ "$fail_before_rename" == "1" ]]; then
    setter_path="/tmp/fail-pin-rotation-bin:$PATH"
  fi
  PATH="$setter_path" python3 - "$setter" "$env_file" <<'PY'
import os
import pty
import secrets
import select
import sys
import time

setter, environment = sys.argv[1:]
pid, descriptor = pty.fork()
if pid == 0:
    os.execv(setter, [setter, environment])

pin_sent = False
output = bytearray()
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    readable, _, _ = select.select([descriptor], [], [], 0.25)
    if readable:
        try:
            chunk = os.read(descriptor, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        if not pin_sent and b"Enter the six-digit temporary web login PIN:" in output:
            pin = f"{secrets.randbelow(1_000_000):06d}"
            os.write(descriptor, pin.encode("ascii") + b"\n")
            pin_sent = True
    finished, status = os.waitpid(pid, os.WNOHANG)
    if finished:
        raise SystemExit(os.waitstatus_to_exitcode(status))
else:
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    raise SystemExit(124)

_, status = os.waitpid(pid, 0)
raise SystemExit(os.waitstatus_to_exitcode(status))
PY
}

assert_private_verifier() {
  local path="$1" directory
  directory="${path%/*}"
  test -f "$path"
  test ! -L "$path"
  test "$(stat -c '%u:%g:%a' "$path")" = 0:0:600
  test "$(stat -c '%u:%g:%a' "$directory")" = 0:0:700
  python3 - "$path" <<'PY'
import re
import sys

with open(sys.argv[1], "rb") as verifier_file:
    value = verifier_file.read()
raise SystemExit(0 if re.fullmatch(rb"[0-9a-f]{64}\n", value) else 1)
PY
}

selected_path() {
  awk -F= '$1 == "TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" {print substr($0, index($0, "=") + 1)}' "$env_file"
}

assert_selector_rejected() {
  local key="$1" value="$2" candidate=/opt/bumpabestie/.env.production.unsafe
  local before=/tmp/unsafe-selector-before file_count_before
  awk -F= -v key="$key" -v value="$value" '
    $1 == key {
      if (++seen > 1) exit 42
      print key "=" value
      next
    }
    { print }
    END { if (seen != 1) exit 43 }
  ' "$env_file" >"$candidate"
  chmod 0600 "$candidate"
  cp "$candidate" "$before"
  file_count_before="$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)"
  if "$setter" "$candidate" >/tmp/unsafe-selector.out 2>&1; then
    echo "Setter accepted an unsafe temporary-PIN WhatsApp selector boundary" >&2
    exit 1
  fi
  grep -Fq 'Stage the fail-closed temporary authentication selectors' /tmp/unsafe-selector.out
  cmp -s "$candidate" "$before"
  test "$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)" = "$file_count_before"
}

install -d -m 0755 /tmp/fail-pin-rotation-bin
printf '%s\n' '#!/usr/bin/env sh' 'exit 70' >/tmp/fail-pin-rotation-bin/mv
chmod 0755 /tmp/fail-pin-rotation-bin/mv

# First activation starts from a deployed disabled boundary with no host path.
# The setter alone allocates and selects the first immutable version.
write_env temporary_static_pin ''
write_release disabled ''
run_setter_with_private_random_pin
first_activation_path="$(selected_path)"
temporary_verifier_host_path_is_versioned "$first_activation_path"
assert_private_verifier "$first_activation_path"

# A legacy fixed path remains a valid deployed boundary for the compatibility
# promotion. It is loaded and rendered without requiring credential rotation.
legacy_path=/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier
install -d -m 0700 "${legacy_path%/*}"
python3 - "$legacy_path" <<'PY'
import os
import secrets
import sys

descriptor = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="ascii") as verifier_file:
    verifier_file.write(secrets.token_hex(32) + "\n")
    verifier_file.flush()
    os.fsync(verifier_file.fileno())
PY
write_env temporary_static_pin "$legacy_path"
write_release temporary_static_pin "$legacy_path"
load_release_boundary "$release_file"
test "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" = "$legacy_path"
validate_auth_boundary_values \
  temporary_static_pin "$runtime_path" "$legacy_path" "$future_expiry" disabled
assert_private_verifier "$legacy_path"

# A corrupted retained verifier must fail before the prompt or allocation. The
# running API may still have an older runtime copy, so host validation cannot be
# deferred until rollback recreation.
cp "$legacy_path" /tmp/legacy-valid
printf 'corrupt\n' >"$legacy_path"
chmod 0600 "$legacy_path"
cp "$env_file" /tmp/env-before-corrupt-check
file_count_before="$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)"
if "$setter" "$env_file" >/tmp/corrupt-old.out 2>&1; then
  echo "Setter accepted a corrupt deployed rollback verifier" >&2
  exit 1
fi
grep -Fq 'deployed rollback verifier is missing or unsafe' /tmp/corrupt-old.out
cmp -s "$env_file" /tmp/env-before-corrupt-check
test "$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)" = "$file_count_before"
cp /tmp/legacy-valid "$legacy_path"
chmod 0600 "$legacy_path"
assert_private_verifier "$legacy_path"

# Rotation from the deployed legacy boundary creates a distinct immutable
# version, preserves the old bytes and permissions, and changes only one env key.
cp "$legacy_path" /tmp/legacy-before
cp "$env_file" /tmp/env-before
run_setter_with_private_random_pin
version_one="$(selected_path)"
temporary_verifier_host_path_is_versioned "$version_one"
test "$version_one" != "$legacy_path"
assert_private_verifier "$version_one"
cmp -s "$legacy_path" /tmp/legacy-before
test "$(stat -c '%u:%g:%a' "$legacy_path")" = 0:0:600
diff -u \
  <(grep -v '^TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=' /tmp/env-before) \
  <(grep -v '^TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=' "$env_file")

# Until the staged version is promoted, another setter run must reject before
# prompting or allocating a file and must not change either boundary.
cp "$env_file" /tmp/staged-env
file_count_before="$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)"
if "$setter" "$env_file" >/tmp/rejected.out 2>&1; then
  echo "Setter accepted a second rotation while one was already staged" >&2
  exit 1
fi
grep -Fq 'already staged' /tmp/rejected.out
cmp -s "$env_file" /tmp/staged-env
test "$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)" = "$file_count_before"

# Simulate a failed pre-child promotion: the recorded legacy boundary is
# restored exactly and its never-overwritten file still validates.
rewrite_release_boundary "$env_file" \
  "$revision" "sha-$revision" "sha-$revision" \
  "$image" "$image" "$image" "$image" "$image" "$image" \
  temporary_static_pin "$runtime_path" "$legacy_path" "$future_expiry" disabled
test "$(selected_path)" = "$legacy_path"
cmp -s "$legacy_path" /tmp/legacy-before
assert_private_verifier "$legacy_path"

# A failure after exclusive verifier creation but before the environment rename
# cleans the candidate and leaves the deployed selection and old bytes intact.
file_count_before="$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)"
set +e
run_setter_with_private_random_pin 1
failure_result=$?
set -e
test "$failure_result" = 70
test "$(selected_path)" = "$legacy_path"
cmp -s "$legacy_path" /tmp/legacy-before
test "$(find /var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers -maxdepth 1 -type f | wc -l)" = "$file_count_before"

# A deployed versioned boundary may enable only the exact reply-only Meta test
# lane. Every widening of that selector boundary is rejected before prompting
# or allocating a verifier.
write_env temporary_static_pin "$version_one" meta
write_release temporary_static_pin "$version_one" meta
assert_selector_rejected META_PRIMARY_SENDER_ENABLED true
assert_selector_rejected META_TEST_SENDER_VERIFICATION_MODE disabled
assert_selector_rejected PROACTIVE_INSIGHTS_ENABLED true
assert_selector_rejected DAILY_INSIGHTS_ENABLED true
assert_selector_rejected WEEKLY_INSIGHTS_ENABLED true

# The safe combined boundary rotates to another unique file, retains the prior
# one byte-for-byte, and rollback selects that prior file again.
cp "$version_one" /tmp/version-one-before
run_setter_with_private_random_pin
version_two="$(selected_path)"
temporary_verifier_host_path_is_versioned "$version_two"
test "$version_two" != "$version_one"
assert_private_verifier "$version_two"
cmp -s "$version_one" /tmp/version-one-before
rewrite_release_boundary "$env_file" \
  "$revision" "sha-$revision" "sha-$revision" \
  "$image" "$image" "$image" "$image" "$image" "$image" \
  temporary_static_pin "$runtime_path" "$version_one" "$future_expiry" meta
test "$(selected_path)" = "$version_one"
cmp -s "$version_one" /tmp/version-one-before
assert_private_verifier "$version_one"

echo "Immutable temporary PIN rotation contract passed."
