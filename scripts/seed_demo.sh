#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

reset=0
if [[ "${1:-}" == "--reset" ]]; then
  reset=1
  if [[ "${RESET_DEMO_CONFIRM:-}" != "reset-local-demo" ]]; then
    echo "Set RESET_DEMO_CONFIRM=reset-local-demo to reset synthetic data" >&2
    exit 2
  fi
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--reset]" >&2
  exit 2
fi

runtime_env="$(docker compose exec -T api python -c 'from app.core.config import get_settings; print(get_settings().app_env)')"
if ((reset)); then
  if [[ "$runtime_env" != "local" && "$runtime_env" != "test" ]]; then
    echo "Demo reset is allowed only when the running API is local or test; found $runtime_env" >&2
    exit 2
  fi
  docker compose exec -T postgres sh -eu -c \
    'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 --command "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
  docker compose --profile tools run --rm migrate
fi

docker compose exec -T api python -m app.seed.demo
