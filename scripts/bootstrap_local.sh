#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

required=(curl docker jq uv node npm make)
missing=()
for command_name in "${required[@]}"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    missing+=("$command_name")
  fi
done

if ((${#missing[@]} > 0)); then
  echo "Missing required tools: ${missing[*]}" >&2
  exit 2
fi

node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
if [[ "$node_major" != "22" ]]; then
  echo "Node 22 is required; found $(node --version). Use .nvmrc." >&2
  exit 2
fi

uv python install 3.12
docker compose version >/dev/null
docker info >/dev/null

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env with local-only mock credentials."
fi

./scripts/validate_env.sh .env local
(
  cd apps/api
  uv sync --all-extras --locked
)
npm --prefix apps/web ci
docker compose config --quiet

echo "Local dependencies are ready. Run: make dev"
