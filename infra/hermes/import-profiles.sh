#!/bin/sh
set -eu

staging_root="${HERMES_STAGING_ROOT:-/staged/profiles}"
runtime_root="${HERMES_RUNTIME_ROOT:-/opt/data/profiles}"

mkdir -p "$runtime_root"
imported=0
for source in "$staging_root"/tenant_*; do
  [ -d "$source" ] || continue
  name="${source##*/}"
  case "$name" in
    *[!a-z0-9_]*)
      echo "Invalid staged Hermes profile name" >&2
      exit 1
      ;;
  esac
  if find "$source" -type l -print -quit | grep -q .; then
    echo "Hermes staging profiles must not contain symlinks" >&2
    exit 1
  fi
  for required in .no-skills .env config.yaml SOUL.md; do
    [ -f "$source/$required" ] || {
      echo "Staged Hermes profile is incomplete" >&2
      exit 1
    }
  done

  destination="$runtime_root/$name"
  mkdir -p "$destination" "$destination/skills" "$destination/memories" \
    "$destination/sessions" "$destination/cron"
  install -m 0600 "$source/.no-skills" "$destination/.no-skills"
  install -m 0600 "$source/.env" "$destination/.env"
  install -m 0600 "$source/config.yaml" "$destination/config.yaml"
  install -m 0600 "$source/SOUL.md" "$destination/SOUL.md"
  chown -R hermes:hermes "$destination"
  find "$destination" -type d -exec chmod 0700 {} +
  imported=$((imported + 1))
done

printf '%s\n' "$imported"
