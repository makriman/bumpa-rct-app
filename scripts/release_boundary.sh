#!/usr/bin/env bash

# Release-pointer persistence is deliberately isolated from the rest of the
# production environment. These helpers never source or print the environment
# file, and every replacement is written to a private file in the same
# directory before an atomic rename.

release_file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

temporary_verifier_host_path_is_legacy() {
  [[ "$1" == "/var/lib/bumpabestie-auth-secret/temporary_web_pin_verifier" ]]
}

temporary_verifier_host_path_is_versioned() {
  [[ "$1" =~ ^/var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/[a-f0-9]{32}$ ]]
}

validate_temporary_verifier_host_path() {
  temporary_verifier_host_path_is_legacy "$1" \
    || temporary_verifier_host_path_is_versioned "$1"
}

validate_release_pointer_values() {
  local revision="$1"
  local image_tag="$2"
  local infra_image_tag="$3"
  shift 3

  if [[ ! "$revision" =~ ^[a-f0-9]{40}$ ]] \
    || [[ "$image_tag" != "sha-$revision" ]] \
    || [[ ! "$infra_image_tag" =~ ^sha-[a-f0-9]{40}$ ]]; then
    return 1
  fi

  local image_ref
  for image_ref in "$@"; do
    if [[ ! "$image_ref" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$ ]]; then
      return 1
    fi
  done
}

validate_auth_boundary_values() {
  local login_mode="$1"
  local verifier_file="$2"
  local verifier_host="$3"
  local expires_at="$4"
  local whatsapp_backend="$5"
  local expiry_pattern

  [[ "$login_mode" =~ ^(disabled|whatsapp_otp|temporary_static_pin)$ ]] || return 1
  [[ "$whatsapp_backend" =~ ^(disabled|meta)$ ]] || return 1
  if [[ "$login_mode" == "temporary_static_pin" ]]; then
    [[ "$verifier_file" == "/run/auth-secret/temporary_web_pin_verifier" ]] || return 1
    validate_temporary_verifier_host_path "$verifier_host" || return 1
    expiry_pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$'
    [[ "$expires_at" =~ $expiry_pattern ]] || return 1
  else
    [[ -z "$verifier_file" && -z "$verifier_host" && -z "$expires_at" ]] || return 1
  fi
}

load_release_boundary() {
  local release_file="$1"
  if [[ ! -f "$release_file" || -L "$release_file" || ! -r "$release_file" ]] \
    || [[ "$(release_file_mode "$release_file")" != "600" ]]; then
    return 1
  fi

  if ! jq --exit-status '
    type == "object" and
    (.revision | type == "string" and test("^[a-f0-9]{40}$")) and
    ((.operations_revision // .revision) |
      type == "string" and test("^[a-f0-9]{40}$")) and
    .image_tag == ("sha-" + .revision) and
    (.infra_image_tag | type == "string" and test("^sha-[a-f0-9]{40}$")) and
    (.images | type == "object") and
    ([
      .images.api, .images.worker, .images.scheduler, .images.web,
      (.images.admin_web // .images.web),
      (.images.research_web // .images.web),
      .images.caddy, .images.postgres, .images.backup, .images.hermes
    ] | all(type == "string" and
      test("^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$"))) and
    (.images.redis | type == "string" and
      test("^[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}$")) and
    .images.worker == .images.api and
    .images.scheduler == .images.api and
    ((has("auth") | not) or
      (.auth |
        type == "object" and
        (keys | sort) == [
          "login_mode", "temporary_web_pin_expires_at",
          "temporary_web_pin_verifier_file", "temporary_web_pin_verifier_file_host",
          "whatsapp_backend"
        ] and
        (.login_mode | type == "string" and
          test("^(disabled|whatsapp_otp|temporary_static_pin)$")) and
        (.whatsapp_backend | type == "string" and test("^(disabled|meta)$")) and
        (.temporary_web_pin_verifier_file | type == "string") and
        (.temporary_web_pin_verifier_file_host | type == "string") and
        (.temporary_web_pin_expires_at | type == "string") and
        (if .login_mode == "temporary_static_pin" then
          .temporary_web_pin_verifier_file ==
            "/run/auth-secret/temporary_web_pin_verifier" and
          (.temporary_web_pin_verifier_file_host |
            test("^/var/lib/bumpabestie-auth-secret/(temporary_web_pin_verifier|temporary-web-pin-verifiers/[a-f0-9]{32})$")) and
          (.temporary_web_pin_expires_at |
            test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"))
        else
          .temporary_web_pin_verifier_file == "" and
          .temporary_web_pin_verifier_file_host == "" and
          .temporary_web_pin_expires_at == ""
        end)
      ))
  ' "$release_file" >/dev/null; then
    return 1
  fi

  # These globals are the deliberate API of this sourced helper; individual
  # callers use different subsets.
  # shellcheck disable=SC2034
  RELEASE_REVISION="$(jq --raw-output '.revision' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_OPERATIONS_REVISION="$(jq --raw-output '.operations_revision // .revision' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_IMAGE_TAG="$(jq --raw-output '.image_tag' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_INFRA_IMAGE_TAG="$(jq --raw-output '.infra_image_tag' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_API_IMAGE="$(jq --raw-output '.images.api' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_WORKER_IMAGE="$(jq --raw-output '.images.worker' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_SCHEDULER_IMAGE="$(jq --raw-output '.images.scheduler' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_WEB_IMAGE="$(jq --raw-output '.images.web' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_ADMIN_WEB_IMAGE="$(jq --raw-output '.images.admin_web // .images.web' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_RESEARCH_WEB_IMAGE="$(jq --raw-output '.images.research_web // .images.web' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_CADDY_IMAGE="$(jq --raw-output '.images.caddy' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_POSTGRES_IMAGE="$(jq --raw-output '.images.postgres' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_REDIS_IMAGE="$(jq --raw-output '.images.redis' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_BACKUP_IMAGE="$(jq --raw-output '.images.backup' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_HERMES_IMAGE="$(jq --raw-output '.images.hermes' "$release_file")"
  # Legacy records predate the explicit auth boundary. They deliberately load
  # as the fail-closed disabled state so the compatibility rollout can only
  # rollback to closed login until a new record has been committed.
  # shellcheck disable=SC2034
  RELEASE_AUTH_LOGIN_MODE="$(jq --raw-output '.auth.login_mode // "disabled"' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE="$(jq --raw-output '.auth.temporary_web_pin_verifier_file // ""' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST="$(jq --raw-output '.auth.temporary_web_pin_verifier_file_host // ""' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_TEMPORARY_WEB_PIN_EXPIRES_AT="$(jq --raw-output '.auth.temporary_web_pin_expires_at // ""' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_WHATSAPP_BACKEND="$(jq --raw-output '.auth.whatsapp_backend // "disabled"' "$release_file")"

  validate_release_pointer_values \
    "$RELEASE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_INFRA_IMAGE_TAG" \
    "$RELEASE_API_IMAGE" "$RELEASE_WEB_IMAGE" "$RELEASE_ADMIN_WEB_IMAGE" \
    "$RELEASE_RESEARCH_WEB_IMAGE" "$RELEASE_CADDY_IMAGE" \
    "$RELEASE_POSTGRES_IMAGE" "$RELEASE_BACKUP_IMAGE" "$RELEASE_HERMES_IMAGE" \
    && validate_auth_boundary_values \
      "$RELEASE_AUTH_LOGIN_MODE" "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE" \
      "$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" \
      "$RELEASE_TEMPORARY_WEB_PIN_EXPIRES_AT" "$RELEASE_WHATSAPP_BACKEND"
}

_rewrite_release_environment() (
  local include_auth="$1"
  shift
  local env_file="$1"
  local revision="$2"
  local image_tag="$3"
  local infra_image_tag="$4"
  local api_image="$5"
  local web_image="$6"
  local admin_web_image="$7"
  local research_web_image="$8"
  local caddy_image="$9"
  local postgres_image="${10}"
  local backup_image="${11}"
  local hermes_image="${12}"
  local auth_login_mode="${13:-}"
  local verifier_file="${14:-}"
  local verifier_host="${15:-}"
  local expires_at="${16:-}"
  local whatsapp_backend="${17:-}"
  local canonicalize_whatsapp_cadences=0
  local meta_primary_sender_enabled=true meta_test_sender_mode=disabled
  local env_tmp env_uid env_gid
  env_tmp=""
  trap 'if [[ -n "$env_tmp" ]]; then rm -f -- "$env_tmp"; fi' EXIT

  if [[ ! -f "$env_file" || -L "$env_file" ]] \
    || [[ "$(release_file_mode "$env_file")" != "600" ]]; then
    return 1
  fi
  validate_release_pointer_values \
    "$revision" "$image_tag" "$infra_image_tag" \
    "$api_image" "$web_image" "$admin_web_image" "$research_web_image" \
    "$caddy_image" "$postgres_image" \
    "$backup_image" "$hermes_image" || return 1
  if [[ "$include_auth" == "1" ]]; then
    validate_auth_boundary_values \
      "$auth_login_mode" "$verifier_file" "$verifier_host" "$expires_at" \
      "$whatsapp_backend" || return 1
    if [[ "$auth_login_mode" == "temporary_static_pin" \
      && "$whatsapp_backend" == "meta" ]]; then
      meta_primary_sender_enabled=false
      meta_test_sender_mode=inbound_replies_only
    fi
    if [[ "$auth_login_mode" == "temporary_static_pin" \
      || "$whatsapp_backend" == "disabled" ]]; then
      canonicalize_whatsapp_cadences=1
    fi
  fi

  env_uid="$(stat -c '%u' "$env_file" 2>/dev/null || stat -f '%u' "$env_file")"
  env_gid="$(stat -c '%g' "$env_file" 2>/dev/null || stat -f '%g' "$env_file")"
  env_tmp="$(mktemp "${env_file}.release.XXXXXX")" || return 1
  if ! chmod 0600 "$env_tmp"; then
    rm -f "$env_tmp"
    return 1
  fi
  if ! awk \
    -v deploy_ref="$revision" \
    -v image_tag="$image_tag" \
    -v infra_image_tag="$infra_image_tag" \
    -v api_image="$api_image" \
    -v web_image="$web_image" \
    -v admin_web_image="$admin_web_image" \
    -v research_web_image="$research_web_image" \
    -v caddy_image="$caddy_image" \
    -v postgres_image="$postgres_image" \
    -v backup_image="$backup_image" \
    -v hermes_image="$hermes_image" \
    -v include_auth="$include_auth" \
    -v auth_login_mode="$auth_login_mode" \
    -v verifier_file="$verifier_file" \
    -v verifier_host="$verifier_host" \
    -v expires_at="$expires_at" \
    -v whatsapp_backend="$whatsapp_backend" \
    -v canonicalize_whatsapp_cadences="$canonicalize_whatsapp_cadences" \
    -v meta_primary_sender_enabled="$meta_primary_sender_enabled" \
    -v meta_test_sender_mode="$meta_test_sender_mode" '
      BEGIN {
        replacement["DEPLOY_REF"] = deploy_ref
        replacement["IMAGE_TAG"] = image_tag
        replacement["INFRA_IMAGE_TAG"] = infra_image_tag
        replacement["API_IMAGE"] = api_image
        replacement["WEB_IMAGE"] = web_image
        replacement["ADMIN_WEB_IMAGE"] = admin_web_image
        replacement["RESEARCH_WEB_IMAGE"] = research_web_image
        replacement["CADDY_IMAGE"] = caddy_image
        replacement["POSTGRES_IMAGE"] = postgres_image
        replacement["BACKUP_IMAGE"] = backup_image
        replacement["HERMES_IMAGE"] = hermes_image
        if (include_auth == 1) {
          replacement["AUTH_LOGIN_MODE"] = auth_login_mode
          replacement["TEMPORARY_WEB_PIN_VERIFIER"] = ""
          replacement["TEMPORARY_WEB_PIN_VERIFIER_FILE"] = verifier_file
          replacement["TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"] = verifier_host
          replacement["TEMPORARY_WEB_PIN_EXPIRES_AT"] = expires_at
          replacement["WHATSAPP_BACKEND"] = whatsapp_backend
          replacement["META_PRIMARY_SENDER_ENABLED"] = meta_primary_sender_enabled
          replacement["META_TEST_SENDER_VERIFICATION_MODE"] = meta_test_sender_mode
          if (canonicalize_whatsapp_cadences == 1) {
            replacement["PROACTIVE_INSIGHTS_ENABLED"] = "false"
            replacement["DAILY_INSIGHTS_ENABLED"] = "false"
            replacement["WEEKLY_INSIGHTS_ENABLED"] = "false"
          }
        }
      }
      {
        separator = index($0, "=")
        key = separator > 0 ? substr($0, 1, separator - 1) : ""
        if (key in replacement) {
          seen[key]++
          if (seen[key] > 1) {
            exit 42
          }
          print key "=" replacement[key]
          next
        }
        print
      }
      END {
        # Releases created before the three-surface split have neither web
        # pointer. Add only those two compatibility keys; every other missing
        # or duplicate boundary field remains a hard failure.
        if (seen["ADMIN_WEB_IMAGE"] == 0) {
          print "ADMIN_WEB_IMAGE=" replacement["ADMIN_WEB_IMAGE"]
          seen["ADMIN_WEB_IMAGE"] = 1
        }
        if (seen["RESEARCH_WEB_IMAGE"] == 0) {
          print "RESEARCH_WEB_IMAGE=" replacement["RESEARCH_WEB_IMAGE"]
          seen["RESEARCH_WEB_IMAGE"] = 1
        }
        for (key in replacement) {
          if (seen[key] != 1) {
            exit 43
          }
        }
      }
    ' "$env_file" > "$env_tmp"; then
    rm -f "$env_tmp"
    return 1
  fi
  if ! chown "$env_uid:$env_gid" "$env_tmp" \
    || ! chmod 0600 "$env_tmp" \
    || ! mv -f "$env_tmp" "$env_file"; then
    rm -f "$env_tmp"
    return 1
  fi
  env_tmp=""
)

rewrite_release_pointers() {
  (($# == 12)) || return 1
  _rewrite_release_environment 0 "$@"
}

rewrite_release_boundary() {
  (($# == 17)) || return 1
  _rewrite_release_environment 1 "$@"
}
