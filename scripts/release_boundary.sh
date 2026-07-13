#!/usr/bin/env bash

# Release-pointer persistence is deliberately isolated from the rest of the
# production environment. These helpers never source or print the environment
# file, and every replacement is written to a private file in the same
# directory before an atomic rename.

release_file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
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
      .images.caddy, .images.postgres, .images.backup, .images.hermes
    ] | all(type == "string" and
      test("^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$"))) and
    (.images.redis | type == "string" and
      test("^[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}$")) and
    .images.worker == .images.api and
    .images.scheduler == .images.api
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
  RELEASE_CADDY_IMAGE="$(jq --raw-output '.images.caddy' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_POSTGRES_IMAGE="$(jq --raw-output '.images.postgres' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_REDIS_IMAGE="$(jq --raw-output '.images.redis' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_BACKUP_IMAGE="$(jq --raw-output '.images.backup' "$release_file")"
  # shellcheck disable=SC2034
  RELEASE_HERMES_IMAGE="$(jq --raw-output '.images.hermes' "$release_file")"

  validate_release_pointer_values \
    "$RELEASE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_INFRA_IMAGE_TAG" \
    "$RELEASE_API_IMAGE" "$RELEASE_WEB_IMAGE" "$RELEASE_CADDY_IMAGE" \
    "$RELEASE_POSTGRES_IMAGE" "$RELEASE_BACKUP_IMAGE" "$RELEASE_HERMES_IMAGE"
}

rewrite_release_pointers() (
  local env_file="$1"
  local revision="$2"
  local image_tag="$3"
  local infra_image_tag="$4"
  local api_image="$5"
  local web_image="$6"
  local caddy_image="$7"
  local postgres_image="$8"
  local backup_image="$9"
  local hermes_image="${10}"
  local env_tmp env_uid env_gid
  env_tmp=""
  trap 'if [[ -n "$env_tmp" ]]; then rm -f -- "$env_tmp"; fi' EXIT

  if [[ ! -f "$env_file" || -L "$env_file" ]] \
    || [[ "$(release_file_mode "$env_file")" != "600" ]]; then
    return 1
  fi
  validate_release_pointer_values \
    "$revision" "$image_tag" "$infra_image_tag" \
    "$api_image" "$web_image" "$caddy_image" "$postgres_image" \
    "$backup_image" "$hermes_image" || return 1

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
    -v caddy_image="$caddy_image" \
    -v postgres_image="$postgres_image" \
    -v backup_image="$backup_image" \
    -v hermes_image="$hermes_image" '
      BEGIN {
        replacement["DEPLOY_REF"] = deploy_ref
        replacement["IMAGE_TAG"] = image_tag
        replacement["INFRA_IMAGE_TAG"] = infra_image_tag
        replacement["API_IMAGE"] = api_image
        replacement["WEB_IMAGE"] = web_image
        replacement["CADDY_IMAGE"] = caddy_image
        replacement["POSTGRES_IMAGE"] = postgres_image
        replacement["BACKUP_IMAGE"] = backup_image
        replacement["HERMES_IMAGE"] = hermes_image
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
