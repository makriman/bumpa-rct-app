#!/usr/bin/env bash
set -Eeuo pipefail

env_file="${1:-.env.production}"

if [[ ! -f "$env_file" || -L "$env_file" ]]; then
  echo "Usage: $0 [regular-environment-file]" >&2
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 2
fi

value_for() {
  local key="$1"
  local count value
  count="$(awk -F= -v wanted="$key" '$1 == wanted {count += 1} END {print count + 0}' "$env_file")"
  if ((count > 1)); then
    echo "Duplicate environment key: $key" >&2
    exit 2
  fi
  value="$(
    awk -F= -v wanted="$key" \
      '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$env_file"
  )"
  printf '%s' "$value"
}

boolean_for() {
  local key="$1"
  local value
  value="$(value_for "$key")"
  case "$value" in
    true | false)
      printf '%s' "$value"
      ;;
    "")
      printf 'false'
      ;;
    *)
      echo "$key must be true or false" >&2
      exit 2
      ;;
  esac
}

secret_ready() {
  local path="$1"
  local permissions
  if [[ "$path" != /* || ! -f "$path" || -L "$path" || ! -s "$path" ]]; then
    printf 'false'
    return
  fi
  permissions="$(stat -c '%a' "$path" 2>/dev/null || stat -f '%Lp' "$path")"
  if [[ ! "$permissions" =~ ^(400|600)$ ]] \
    || [[ "$(awk 'END {print NR + 0}' "$path")" -ne 1 ]]; then
    printf 'false'
    return
  fi
  printf 'true'
}

activation_state() {
  local enabled="$1"
  local ready="$2"
  if [[ "$enabled" == "true" && "$ready" == "true" ]]; then
    printf 'active'
  elif [[ "$enabled" == "true" ]]; then
    printf 'misconfigured'
  elif [[ "$ready" == "true" ]]; then
    printf 'ready_disabled'
  else
    printf 'blocked_missing_prerequisite'
  fi
}

switch_state() {
  if [[ "$1" == "true" ]]; then
    printf 'active'
  else
    printf 'disabled'
  fi
}

agent_enabled="$(boolean_for AGENT_CAPABILITIES_V2)"
hermes_tools_enabled="$(boolean_for HERMES_TOOLS_ENABLED)"
research_enabled="$(boolean_for WEB_RESEARCH_ENABLED)"
sandbox_enabled="$(boolean_for SANDBOX_TOOLS_ENABLED)"
image_enabled="$(boolean_for MANAGED_IMAGE_GENERATION_ENABLED)"
connectors_enabled="$(boolean_for EXTERNAL_CONNECTORS_ENABLED)"
google_enabled="$(boolean_for MCP_GOOGLE_OAUTH_ENABLED)"
meta_ads_enabled="$(boolean_for MCP_META_ADS_OAUTH_ENABLED)"
primary_pilot_enabled="$(boolean_for WHATSAPP_PRIMARY_PILOT_ENABLED)"
multimodal_enabled="$(boolean_for WHATSAPP_MULTIMODAL_ENABLED)"
speech_enabled="$(boolean_for WHATSAPP_SPEECH_ENABLED)"
progress_enabled="$(boolean_for WHATSAPP_PROGRESS_ENABLED)"
proactive_enabled="$(boolean_for PROACTIVE_INSIGHTS_ENABLED)"
daily_enabled="$(boolean_for DAILY_INSIGHTS_ENABLED)"
weekly_enabled="$(boolean_for WEEKLY_INSIGHTS_ENABLED)"

tavily_ready="$(secret_ready "$(value_for TAVILY_API_KEY_FILE_HOST)")"
elevenlabs_secret_ready="$(secret_ready "$(value_for ELEVENLABS_API_KEY_FILE_HOST)")"
sandbox_secret_ready="$(secret_ready "$(value_for SANDBOX_SERVICE_TOKEN_FILE_HOST)")"
google_secret_ready="$(secret_ready "$(value_for GOOGLE_OAUTH_CLIENT_SECRET_FILE_HOST)")"
meta_ads_secret_ready="$(secret_ready "$(value_for META_ADS_OAUTH_CLIENT_SECRET_FILE_HOST)")"

sandbox_url="$(value_for SANDBOX_WORKER_URL)"
sandbox_url_ready=false
if [[ "$sandbox_url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
  sandbox_url_ready=true
fi
sandbox_ready=false
if [[ "$sandbox_secret_ready" == "true" && "$sandbox_url_ready" == "true" ]]; then
  sandbox_ready=true
fi

voice_id="$(value_for ELEVENLABS_TTS_VOICE_ID)"
voice_ready=false
if [[ "$voice_id" =~ ^[A-Za-z0-9_-]{6,128}$ ]]; then
  voice_ready=true
fi
elevenlabs_ready=false
if [[ "$elevenlabs_secret_ready" == "true" && "$voice_ready" == "true" ]]; then
  elevenlabs_ready=true
fi

google_client_id="$(value_for GOOGLE_OAUTH_CLIENT_ID)"
google_ready=false
if [[ -n "$google_client_id" && "$google_client_id" != *[[:space:]]* \
  && "$google_secret_ready" == "true" ]]; then
  google_ready=true
fi

meta_ads_client_id="$(value_for META_ADS_OAUTH_CLIENT_ID)"
meta_ads_ready=false
if [[ "$meta_ads_client_id" =~ ^[0-9]{5,32}$ && "$meta_ads_secret_ready" == "true" ]]; then
  meta_ads_ready=true
fi

auth_login_mode="$(value_for AUTH_LOGIN_MODE)"
case "$auth_login_mode" in
  disabled | temporary_static_pin | whatsapp_otp) ;;
  *)
    echo "AUTH_LOGIN_MODE is missing or invalid" >&2
    exit 2
    ;;
esac
otp_state=disabled
if [[ "$auth_login_mode" == "whatsapp_otp" ]]; then
  otp_state=active
fi

jq -n \
  --arg agent "$(switch_state "$agent_enabled")" \
  --arg hermes_tools "$(switch_state "$hermes_tools_enabled")" \
  --arg research "$(activation_state "$research_enabled" "$tavily_ready")" \
  --arg sandbox "$(activation_state "$sandbox_enabled" "$sandbox_ready")" \
  --arg images "$(activation_state "$image_enabled" "$sandbox_ready")" \
  --arg connector_control_plane "$(switch_state "$connectors_enabled")" \
  --arg google "$(activation_state "$google_enabled" "$google_ready")" \
  --arg meta_ads "$(activation_state "$meta_ads_enabled" "$meta_ads_ready")" \
  --arg primary_pilot "$(switch_state "$primary_pilot_enabled")" \
  --arg multimodal "$(switch_state "$multimodal_enabled")" \
  --arg speech "$(activation_state "$speech_enabled" "$elevenlabs_ready")" \
  --arg progress "$(switch_state "$progress_enabled")" \
  --arg proactive "$(switch_state "$proactive_enabled")" \
  --arg daily "$(switch_state "$daily_enabled")" \
  --arg weekly "$(switch_state "$weekly_enabled")" \
  --arg otp "$otp_state" \
  --arg auth_login_mode "$auth_login_mode" \
  --argjson tavily_ready "$tavily_ready" \
  --argjson elevenlabs_secret_ready "$elevenlabs_secret_ready" \
  --argjson voice_ready "$voice_ready" \
  --argjson sandbox_ready "$sandbox_ready" \
  --argjson google_ready "$google_ready" \
  --argjson meta_ads_ready "$meta_ads_ready" \
  '{
    agent: {
      consultant: $agent,
      hermes_tools: $hermes_tools
    },
    research: {
      state: $research,
      tavily_credential_ready: $tavily_ready
    },
    sandbox: {
      state: $sandbox,
      managed_image_generation: $images,
      worker_and_credential_ready: $sandbox_ready
    },
    connectors: {
      control_plane: $connector_control_plane,
      google_oauth: $google,
      google_prerequisites_ready: $google_ready,
      meta_ads_oauth: $meta_ads,
      meta_ads_prerequisites_ready: $meta_ads_ready
    },
    whatsapp: {
      primary_pilot: $primary_pilot,
      multimodal: $multimodal,
      speech: $speech,
      progress: $progress,
      elevenlabs_credential_ready: $elevenlabs_secret_ready,
      tts_voice_ready: $voice_ready,
      proactive: $proactive,
      daily: $daily,
      weekly: $weekly,
      otp: $otp,
      auth_login_mode: $auth_login_mode
    }
  }'
