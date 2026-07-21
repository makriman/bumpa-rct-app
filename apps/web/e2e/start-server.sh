#!/usr/bin/env bash
set -Eeuo pipefail

node e2e/session-fixture.mjs &
fixture_pid=$!
server_pid=""

cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
  fi
  kill "$fixture_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

npm run build
mkdir -p .next/standalone/public .next/standalone/.next/static
cp -R public/. .next/standalone/public/
cp -R .next/static/. .next/standalone/.next/static/

API_BASE_URL=http://127.0.0.1:3099 \
  PORT=3010 \
  HOSTNAME=127.0.0.1 \
  node .next/standalone/server.js &
server_pid=$!
wait "$server_pid"
