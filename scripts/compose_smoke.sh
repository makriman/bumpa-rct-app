#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The smoke stack is disposable evidence, not the developer's durable local
# environment. Always isolate its containers, networks, and named volumes so a
# stale or in-progress local schema can neither contaminate the gate nor be
# destroyed by cleanup.
if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
  case "$COMPOSE_PROJECT_NAME" in
    bumpabestie-smoke-*) ;;
    *)
      echo "COMPOSE_PROJECT_NAME for compose smoke must start with bumpabestie-smoke-" >&2
      exit 2
      ;;
  esac
else
  export COMPOSE_PROJECT_NAME="bumpabestie-smoke-${PPID}-$$"
fi

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
  docker compose --profile async --profile tools down --volumes --remove-orphans
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
./scripts/local_e2e.sh
