#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

created_env=0
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  created_env=1
fi

cleanup() {
  result=$?
  if ((result != 0)); then
    docker compose ps >&2 || true
    docker compose logs --no-color --tail=200 >&2 || true
  fi
  docker compose down --remove-orphans
  if ((created_env)); then
    rm -f .env
  fi
  exit "$result"
}
trap cleanup EXIT

docker compose config --quiet
docker compose up -d --build postgres redis
docker compose build api
docker compose --profile tools run --rm migrate
docker compose up -d --build api worker scheduler web caddy
docker compose ps
./scripts/smoke_test.sh
