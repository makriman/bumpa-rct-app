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

find "$target_dir" -mindepth 1 -maxdepth 1 -type f \
  ! -name meta_app_secret \
  ! -name meta_system_user_access_token \
  ! -name meta_webhook_verify_token \
  -delete
chown "$runtime_uid:$runtime_gid" "$target_dir"
chmod 0500 "$target_dir"
