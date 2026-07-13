#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/promotion_state.sh"

test_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$test_dir"
}
trap cleanup EXIT

export BUMPABESTIE_MAINTENANCE_LOCK="$test_dir/maintenance.lock"
state_file="$BUMPABESTIE_MAINTENANCE_LOCK.promotion-state.123"
for state in PRE_BOUNDARY FORWARD_BOUNDARY PREVIOUS_RESTORED HYBRID_PERSISTED COMMITTED; do
  write_promotion_state "$state_file" "$state"
  test "$(read_promotion_state "$state_file")" = "$state"
  test "$(stat -c '%a' "$state_file" 2>/dev/null || stat -f '%Lp' "$state_file")" = 600
done

printf 'CORRUPT\n' > "$state_file"
if read_promotion_state "$state_file" >/dev/null 2>&1; then
  echo "Corrupt promotion state was accepted" >&2
  exit 1
fi
if write_promotion_state "$test_dir/unsafe" PRE_BOUNDARY; then
  echo "Unsafe promotion state path was accepted" >&2
  exit 1
fi

rm -f "$state_file"
assert_maintenance_clear
mark_maintenance_required 'fault-injection'
set +e
assert_maintenance_clear >/dev/null 2>&1
interlock_result=$?
set -e
test "$interlock_result" = 78

echo "Promotion state contract passed"
