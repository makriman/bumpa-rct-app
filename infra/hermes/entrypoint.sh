#!/bin/sh
set -eu

secret_file="${ANTHROPIC_API_KEY_FILE:-/run/secrets/hermes_anthropic_api_key}"
if [ ! -f "$secret_file" ]; then
  echo "Hermes Anthropic secret file is unavailable" >&2
  exit 1
fi

anthropic_api_key="$(cat "$secret_file")"
case "$anthropic_api_key" in
  sk-ant-?*) ;;
  *)
    echo "Hermes Anthropic secret is invalid" >&2
    exit 1
    ;;
esac
if [ "${#anthropic_api_key}" -lt 32 ]; then
  echo "Hermes Anthropic secret is invalid" >&2
  exit 1
fi

export ANTHROPIC_API_KEY="$anthropic_api_key"
unset anthropic_api_key

exec /init /opt/hermes/docker/main-wrapper.sh "$@"
