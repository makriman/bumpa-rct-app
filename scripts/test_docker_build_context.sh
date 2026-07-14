#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v docker >/dev/null 2>&1 || {
  echo "Docker build-context contract requires Docker" >&2
  exit 2
}

tmp="$(mktemp -d)"
cleanup() {
  rm -rf -- "$tmp"
}
trap cleanup EXIT

cp "$ROOT_DIR/.dockerignore" "$tmp/.dockerignore"

# First prove the local builder can complete a context-free scratch build, so a
# later expected failure cannot be mistaken for an unavailable Docker daemon.
printf 'FROM scratch\n' |
  docker build --quiet --file - "$tmp" >/dev/null

for private_input in \
  'bumpa bestie secrets.md' \
  'Secrets.md' \
  '.secrets/runtime-token' \
  '.evidence/private.json' \
  'operator.pem' \
  'operator.key' \
  'operator.p12' \
  '.deployed-release.json'; do
  mkdir -p -- "$(dirname "$tmp/$private_input")"
  printf 'synthetic private input; never a real secret\n' >"$tmp/$private_input"
  if printf 'FROM scratch\nCOPY ["%s", "/forbidden"]\n' "$private_input" |
      docker build --quiet --file - "$tmp" >"$tmp/build.out" 2>"$tmp/build.err"; then
    echo "A private local input entered the Docker build context" >&2
    exit 1
  fi
  rm -f -- "$tmp/$private_input" "$tmp/build.out" "$tmp/build.err"
done

echo "Docker build context excludes local credentials and private state."
