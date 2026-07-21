#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary_directory="$(mktemp -d)"
temporary="$temporary_directory/api-contract.ts"
trap 'rm -rf "$temporary_directory"' EXIT

cd "$repo_root/apps/web"
npm exec -- openapi-typescript ../../contracts/openapi.json --output "$temporary"
npm exec -- prettier --write "$temporary" >/dev/null

generated="$repo_root/packages/web-foundation/src/generated/api-contract.ts"
if ! cmp --silent "$generated" "$temporary"; then
  echo "Shared generated TypeScript API contract has drifted; run \`make api-contract\`." >&2
  diff -u "$generated" "$temporary" || true
  exit 1
fi
echo "Shared generated TypeScript API contract is current: $generated"
