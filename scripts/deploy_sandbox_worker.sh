#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="$ROOT_DIR/apps/sandbox-worker"
secret_file="${SANDBOX_SERVICE_TOKEN_FILE_HOST:-}"

if [[ "$secret_file" != /* || ! -f "$secret_file" || -L "$secret_file" ]]; then
  echo "SANDBOX_SERVICE_TOKEN_FILE_HOST must be an absolute regular non-symlink file" >&2
  exit 2
fi
permissions="$(stat -c '%a' "$secret_file" 2>/dev/null || stat -f '%Lp' "$secret_file")"
if [[ ! "$permissions" =~ ^(400|600)$ ]]; then
  echo "SANDBOX_SERVICE_TOKEN_FILE_HOST must have mode 0400 or 0600" >&2
  exit 2
fi
if [[ ! -s "$secret_file" || "$(wc -l < "$secret_file")" -gt 1 ]]; then
  echo "SANDBOX_SERVICE_TOKEN_FILE_HOST must contain one non-empty line" >&2
  exit 2
fi

cd "$WORKER_DIR"
npm ci
npm run typecheck
npm test
npx wrangler secret put SANDBOX_SERVICE_TOKEN < "$secret_file"
npx wrangler deploy
