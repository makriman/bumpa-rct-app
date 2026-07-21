#!/usr/bin/env bash

# Keep the host-side release gate aligned with the public API readiness
# selector. The API deliberately distinguishes the primary Meta sender from
# the test-number-only reply lane even though both use WHATSAPP_BACKEND=meta.
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
