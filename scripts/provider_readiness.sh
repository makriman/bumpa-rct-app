#!/usr/bin/env bash

# Mirror the API's public provider selector at the host-side release boundary.
# Both the primary Meta sender and the test-number reply lane use
# WHATSAPP_BACKEND=meta, so the primary-sender flag disambiguates their public
# readiness values.
expected_whatsapp_readiness_selector() {
  if (($# != 2)); then
    echo "Usage: expected_whatsapp_readiness_selector <backend> <primary-sender-enabled>" >&2
    return 2
  fi

  local backend="$1"
  local primary_sender_enabled="$2"
  if [[ ! "$primary_sender_enabled" =~ ^(true|false)$ ]]; then
    echo "Meta primary-sender selector must be true or false" >&2
    return 2
  fi

  case "$backend" in
    disabled)
      printf 'disabled\n'
      ;;
    meta)
      if [[ "$primary_sender_enabled" == "false" ]]; then
        printf 'meta_test_reply_only\n'
      else
        printf 'meta\n'
      fi
      ;;
    *)
      echo "Unsupported production WhatsApp backend: $backend" >&2
      return 2
      ;;
  esac
}

provider_readiness_matches() {
  if (($# != 4)); then
    echo "Usage: provider_readiness_matches <payload> <whatsapp> <bumpa> <agent>" >&2
    return 2
  fi

  local payload="$1"
  local whatsapp="$2"
  local bumpa="$3"
  local agent="$4"
  jq --exit-status \
    --arg whatsapp "$whatsapp" \
    --arg bumpa "$bumpa" \
    --arg agent "$agent" \
    '.status == "ready" and .database == "ok" and
     .providers.whatsapp == $whatsapp and
     .providers.bumpa == $bumpa and
     .providers.agent == $agent' <<<"$payload" >/dev/null
}
