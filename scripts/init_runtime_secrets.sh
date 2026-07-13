#!/usr/bin/env sh
set -eu

source_dir="${SOURCE_SECRET_DIR:-/run/secrets}"
target_dir="${RUNTIME_SECRET_DIR:-/runtime-secrets}"
runtime_uid="${RUNTIME_SECRET_UID:-100}"
runtime_gid="${RUNTIME_SECRET_GID:-101}"

umask 077
mkdir -p "$target_dir"

for secret_name in \
  meta_app_secret \
  meta_system_user_access_token \
  meta_webhook_verify_token
do
  source_path="$source_dir/$secret_name"
  target_path="$target_dir/$secret_name"
  temporary_path="$target_dir/.${secret_name}.tmp"

  if [ ! -f "$source_path" ] || [ -L "$source_path" ]; then
    echo "Required runtime secret is missing or invalid: $secret_name" >&2
    exit 1
  fi

  rm -f "$temporary_path"
  cp "$source_path" "$temporary_path"
  chown "$runtime_uid:$runtime_gid" "$temporary_path"
  chmod 0400 "$temporary_path"
  mv -f "$temporary_path" "$target_path"
done

for secret_name in \
  ops_alert_hmac_secret \
  google_oauth_client_secret \
  meta_ads_oauth_client_secret
do
  optional_source="${OPTIONAL_SECRET_DIR:-/run/optional-secrets}/$secret_name"
  optional_target="$target_dir/$secret_name"
  if [ -f "$optional_source" ] && [ ! -L "$optional_source" ] && [ -s "$optional_source" ]; then
    temporary_path="$target_dir/.${secret_name}.tmp"
    rm -f "$temporary_path"
    cp "$optional_source" "$temporary_path"
    chown "$runtime_uid:$runtime_gid" "$temporary_path"
    chmod 0400 "$temporary_path"
    mv -f "$temporary_path" "$optional_target"
  else
    rm -f "$optional_target"
  fi
done

find "$target_dir" -mindepth 1 -maxdepth 1 -type f \
  ! -name meta_app_secret \
  ! -name meta_system_user_access_token \
  ! -name meta_webhook_verify_token \
  ! -name ops_alert_hmac_secret \
  ! -name google_oauth_client_secret \
  ! -name meta_ads_oauth_client_secret \
  -delete
chown "$runtime_uid:$runtime_gid" "$target_dir"
chmod 0500 "$target_dir"
