#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

inherited_lock_fd="${BUMPABESTIE_MAINTENANCE_LOCK_FD:-}"
promotion_state_file="${BUMPABESTIE_PROMOTION_STATE_FILE:-}"
if [[ -z "$inherited_lock_fd" || -z "$promotion_state_file" \
  || -z "${BUMPABESTIE_PREVIOUS_CHECKOUT:-}" ]]; then
  echo "Direct deployment is forbidden; use the guarded release promotion entrypoint" >&2
  exit 2
fi

# Deployment and scheduled backup both stop and resume application writers.
# Serialize them before either workflow reads mutable checkout or environment
# state so one workflow cannot restart services during the other's critical
# section.
source "$ROOT_DIR/scripts/maintenance_lock.sh"
acquire_maintenance_lock
source "$ROOT_DIR/scripts/promotion_state.sh"
if [[ "$(read_promotion_state "$promotion_state_file")" != "PRE_BOUNDARY" ]]; then
  echo "Promotion handoff is not at the pre-boundary phase" >&2
  exit 2
fi
source "$ROOT_DIR/scripts/release_boundary.sh"
source "$ROOT_DIR/scripts/rollback_containment.sh"

previous_boundary_valid=0
automatic_rollback_available=0
previous_revision=""
previous_image_tag=""
previous_infra_image_tag=""
previous_api_image=""
previous_worker_image=""
previous_scheduler_image=""
previous_web_image=""
previous_caddy_image=""
previous_postgres_image=""
previous_redis_image=""
previous_backup_image=""
previous_hermes_image=""
previous_auth_login_mode=""
previous_temporary_web_pin_verifier_file=""
previous_temporary_web_pin_verifier_file_host=""
previous_temporary_web_pin_expires_at=""
previous_whatsapp_backend=""
promotion_previous_checkout="${BUMPABESTIE_PREVIOUS_CHECKOUT:-}"

restore_previous_checkout() {
  if [[ -z "$promotion_previous_checkout" ]]; then
    return 0
  fi
  if [[ ! "$promotion_previous_checkout" =~ ^[a-f0-9]{40}$ ]] \
    || ! git rev-parse --verify "${promotion_previous_checkout}^{commit}" >/dev/null 2>&1; then
    echo "Previous checkout handoff is invalid; operator intervention is required." >&2
    return 1
  fi
  git checkout --detach "$promotion_previous_checkout" >/dev/null
}

restore_previous_release_boundary() {
  if ((previous_boundary_valid == 0)); then
    return 1
  fi
  rewrite_release_boundary .env.production \
    "$previous_revision" "$previous_image_tag" "$previous_infra_image_tag" \
    "$previous_api_image" "$previous_web_image" "$previous_caddy_image" \
    "$previous_postgres_image" "$previous_backup_image" "$previous_hermes_image" \
    "$previous_auth_login_mode" "$previous_temporary_web_pin_verifier_file" \
    "$previous_temporary_web_pin_verifier_file_host" \
    "$previous_temporary_web_pin_expires_at" "$previous_whatsapp_backend" || return 1
  AUTH_LOGIN_MODE="$previous_auth_login_mode"
  TEMPORARY_WEB_PIN_VERIFIER=""
  TEMPORARY_WEB_PIN_VERIFIER_FILE="$previous_temporary_web_pin_verifier_file"
  TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST="$previous_temporary_web_pin_verifier_file_host"
  TEMPORARY_WEB_PIN_EXPIRES_AT="$previous_temporary_web_pin_expires_at"
  WHATSAPP_BACKEND="$previous_whatsapp_backend"
  export AUTH_LOGIN_MODE TEMPORARY_WEB_PIN_VERIFIER \
    TEMPORARY_WEB_PIN_VERIFIER_FILE TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST \
    TEMPORARY_WEB_PIN_EXPIRES_AT WHATSAPP_BACKEND
}

# Install a set -u-safe trap before target validation. Once a trusted release
# record is loaded, every later preflight failure restores its complete release
# boundary even when the operator selected the failed target before invoking us.
early_failure_restore() {
  local result=$?
  local restored=1
  trap - EXIT
  rm -f .env.production.release.* .deployed-revision.tmp.* .deployed-release.json.tmp.*
  if ((result != 0 && previous_boundary_valid)); then
    if restore_previous_release_boundary; then
      echo "Restored the previously verified release boundary after preflight failure." >&2
    else
      restored=0
      echo "Unable to restore the previously verified release boundary; operator intervention is required." >&2
    fi
  fi
  if ((result != 0)); then
    if ! restore_previous_checkout; then
      restored=0
      echo "Unable to restore the previous checkout after preflight failure." >&2
    fi
    if ((previous_boundary_valid && restored)); then
      write_promotion_state "$promotion_state_file" PREVIOUS_RESTORED || \
        mark_maintenance_required "preflight_terminal_state_write_failed" || true
    elif ((previous_boundary_valid)); then
      mark_maintenance_required "preflight_restore_failed" || true
    fi
  fi
  exit "$result"
}
trap early_failure_restore EXIT

if [[ ! -f .env.production ]]; then
  echo ".env.production is required and must have mode 0600" >&2
  exit 2
fi
permissions="$(stat -c '%a' .env.production 2>/dev/null || stat -f '%Lp' .env.production)"
if [[ "$permissions" != "600" ]]; then
  echo ".env.production permissions must be 0600; found $permissions" >&2
  exit 2
fi

if [[ -e .deployed-release.json ]]; then
  if ! load_release_boundary .deployed-release.json; then
    echo ".deployed-release.json is not a valid private release boundary" >&2
    exit 2
  fi
  previous_revision="$RELEASE_REVISION"
  previous_image_tag="$RELEASE_IMAGE_TAG"
  previous_infra_image_tag="$RELEASE_INFRA_IMAGE_TAG"
  previous_api_image="$RELEASE_API_IMAGE"
  previous_worker_image="$RELEASE_WORKER_IMAGE"
  previous_scheduler_image="$RELEASE_SCHEDULER_IMAGE"
  previous_web_image="$RELEASE_WEB_IMAGE"
  previous_caddy_image="$RELEASE_CADDY_IMAGE"
  previous_postgres_image="$RELEASE_POSTGRES_IMAGE"
  previous_redis_image="$RELEASE_REDIS_IMAGE"
  previous_backup_image="$RELEASE_BACKUP_IMAGE"
  previous_hermes_image="$RELEASE_HERMES_IMAGE"
  previous_auth_login_mode="$RELEASE_AUTH_LOGIN_MODE"
  previous_temporary_web_pin_verifier_file="$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE"
  previous_temporary_web_pin_verifier_file_host="$RELEASE_TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST"
  previous_temporary_web_pin_expires_at="$RELEASE_TEMPORARY_WEB_PIN_EXPIRES_AT"
  previous_whatsapp_backend="$RELEASE_WHATSAPP_BACKEND"
  previous_boundary_valid=1
fi

./scripts/validate_env.sh .env.production production

value_for() {
  local key="$1"
  awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' .env.production
}
for key in \
  DEPLOY_REF IMAGE_TAG INFRA_IMAGE_TAG \
  API_IMAGE WEB_IMAGE CADDY_IMAGE POSTGRES_IMAGE BACKUP_IMAGE HERMES_IMAGE SECRETS_DIR \
  APP_DOMAIN WWW_DOMAIN ADMIN_DOMAIN RESEARCH_DOMAIN API_DOMAIN \
  AUTH_LOGIN_MODE TEMPORARY_WEB_PIN_VERIFIER_FILE \
  TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST TEMPORARY_WEB_PIN_EXPIRES_AT \
  WHATSAPP_BACKEND BUMPA_BACKEND AGENT_BACKEND; do
  value="$(value_for "$key")"
  printf -v "$key" '%s' "$value"
  export "${key?}"
done

if [[ "$(value_for OPS_ALERTS_ENABLED)" == "true" ]]; then
  host_alert_config="/etc/bumpabestie/alerts.json"
  if [[ ! -f "$host_alert_config" || -L "$host_alert_config" ]]; then
    echo "Operational alerts are enabled but $host_alert_config is missing or unsafe" >&2
    exit 2
  fi
  alert_config_permissions="$(stat -c '%a' "$host_alert_config" 2>/dev/null || stat -f '%Lp' "$host_alert_config")"
  if [[ "$alert_config_permissions" != "640" ]]; then
    echo "Host alert configuration permissions must be 0640; found $alert_config_permissions" >&2
    exit 2
  fi
  if ! jq --exit-status \
    --arg endpoint "$(value_for OPS_ALERT_WEBHOOK_URL)" \
    --arg secret_file "$(value_for OPS_ALERT_HMAC_SECRET_FILE_HOST)" \
    '(keys | sort) == ["hmac_secret_file", "max_attempts", "timeout_seconds", "webhook_url"]
     and .webhook_url == $endpoint
     and .hmac_secret_file == $secret_file
     and (.max_attempts | type == "number" and . >= 1 and . <= 5)
     and (.timeout_seconds | type == "number" and . >= 1 and . <= 30)' \
    "$host_alert_config" >/dev/null; then
    echo "Host alert configuration does not match the enabled production contract" >&2
    exit 2
  fi
fi

target_revision="$DEPLOY_REF"
target_image_tag="$IMAGE_TAG"
target_infra_image_tag="$INFRA_IMAGE_TAG"
target_api_image="$API_IMAGE"
target_web_image="$WEB_IMAGE"
# Values are assigned dynamically by the validated environment-key loop above.
# shellcheck disable=SC2153
target_caddy_image="$CADDY_IMAGE"
# shellcheck disable=SC2153
target_postgres_image="$POSTGRES_IMAGE"
# shellcheck disable=SC2153
target_backup_image="$BACKUP_IMAGE"
target_hermes_image="$HERMES_IMAGE"

if [[ ! -d "$SECRETS_DIR" || -L "$SECRETS_DIR" ]]; then
  echo "SECRETS_DIR must be a real directory" >&2
  exit 2
fi
secret_dir_permissions="$(stat -c '%a' "$SECRETS_DIR" 2>/dev/null || stat -f '%Lp' "$SECRETS_DIR")"
if [[ "$secret_dir_permissions" != "700" ]]; then
  echo "SECRETS_DIR permissions must be 0700; found $secret_dir_permissions" >&2
  exit 2
fi
for secret_name in \
  meta_app_secret meta_system_user_access_token meta_webhook_verify_token \
  hermes_anthropic_api_key; do
  secret_path="$SECRETS_DIR/$secret_name"
  if [[ ! -f "$secret_path" || -L "$secret_path" || ! -s "$secret_path" ]]; then
    echo "Required production secret file is missing or unsafe: $secret_name" >&2
    exit 2
  fi
  secret_permissions="$(stat -c '%a' "$secret_path" 2>/dev/null || stat -f '%Lp' "$secret_path")"
  if [[ "$secret_permissions" != "600" ]]; then
    echo "Production secret file permissions must be 0600: $secret_name" >&2
    exit 2
  fi
done

if [[ -z "${DEPLOY_REF:-}" || -z "${IMAGE_TAG:-}" || -z "${INFRA_IMAGE_TAG:-}" ]]; then
  echo "DEPLOY_REF, IMAGE_TAG and INFRA_IMAGE_TAG are required" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing deployment from a dirty checkout" >&2
  exit 2
fi

git rev-parse --verify "${DEPLOY_REF}^{commit}" >/dev/null
deploy_commit="$(git rev-parse "${DEPLOY_REF}^{commit}")"
if [[ "$IMAGE_TAG" != "sha-$deploy_commit" ]]; then
  echo "IMAGE_TAG must be sha-$deploy_commit for DEPLOY_REF=$DEPLOY_REF" >&2
  exit 2
fi
if [[ "$(git rev-parse HEAD)" != "$deploy_commit" ]]; then
  echo "Promotion checkout does not match DEPLOY_REF" >&2
  exit 2
fi

for unit_name in \
  bumpabestie-backup.service bumpabestie-backup.timer \
  bumpabestie-disk-usage.service bumpabestie-disk-usage.timer; do
  repository_unit="infra/systemd/$unit_name"
  installed_unit="/etc/systemd/system/$unit_name"
  if [[ ! -f "$installed_unit" || ! -r "$installed_unit" ]] || \
    ! cmp --silent "$repository_unit" "$installed_unit"; then
    echo "Installed systemd unit is missing or stale: $unit_name" >&2
    echo "Install the reviewed unit as root and run systemctl daemon-reload before retrying" >&2
    exit 2
  fi
done
for timer_name in bumpabestie-backup.timer bumpabestie-disk-usage.timer; do
  if ! systemctl is-enabled --quiet "$timer_name" || \
    ! systemctl is-active --quiet "$timer_name"; then
    echo "Required host timer is not enabled and active: $timer_name" >&2
    exit 2
  fi
done

compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)

run_production_smoke() {
  if ! SMOKE_SCHEME=https \
      SMOKE_PORT=443 \
      SMOKE_OVERALL_TIMEOUT_SECONDS=180 \
      SMOKE_ORIGIN_ADDRESS=127.0.0.1 \
      ./scripts/smoke_test.sh; then
    return 1
  fi
  SMOKE_SCHEME=https \
    SMOKE_PORT=443 \
    SMOKE_OVERALL_TIMEOUT_SECONDS=60 \
    SMOKE_ORIGIN_ADDRESS='' \
    ./scripts/smoke_test.sh
}

running_image() {
  local service="$1"
  local container_id configured_ref image_id repository immutable_ref last_component
  container_id="$("${compose[@]}" ps --status running -q "$service")"
  if [[ -n "$container_id" ]]; then
    configured_ref="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
    if [[ "$configured_ref" =~ @sha256:[a-f0-9]{64}$ ]]; then
      printf '%s\n' "$configured_ref"
      return
    fi

    repository="$configured_ref"
    last_component="${repository##*/}"
    if [[ "$last_component" == *:* ]]; then
      repository="${repository%:*}"
    fi
    image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
    immutable_ref="$(
      docker image inspect --format '{{json .RepoDigests}}' "$image_id" |
        jq --raw-output --arg prefix "$repository@sha256:" \
          '[.[] | select(startswith($prefix))][0] // ""'
    )"
    if [[ ! "$immutable_ref" =~ @sha256:[a-f0-9]{64}$ ]]; then
      echo "Unable to resolve an immutable rollback image for running $service" >&2
      return 1
    fi
    printf '%s\n' "$immutable_ref"
  fi
}

if ((previous_boundary_valid)); then
  for service in api worker scheduler web hermes caddy postgres redis; do
    case "$service" in
      api) recorded_image="$previous_api_image" ;;
      worker) recorded_image="$previous_worker_image" ;;
      scheduler) recorded_image="$previous_scheduler_image" ;;
      web) recorded_image="$previous_web_image" ;;
      hermes) recorded_image="$previous_hermes_image" ;;
      caddy) recorded_image="$previous_caddy_image" ;;
      postgres) recorded_image="$previous_postgres_image" ;;
      redis) recorded_image="$previous_redis_image" ;;
    esac
    actual_image="$(running_image "$service")"
    if [[ -z "$actual_image" || "$actual_image" != "$recorded_image" ]]; then
      echo "Running $service image does not match the verified release boundary" >&2
      exit 2
    fi
  done
  automatic_rollback_available=1
fi

if ((previous_boundary_valid == 0)); then
  previous_api_image="$(running_image api)"
  previous_web_image="$(running_image web)"
  previous_hermes_image="$(running_image hermes)"
fi
previous_worker_running=0
previous_scheduler_running=0
previous_hermes_running=0
previous_writer_containers=()
for service in api web worker scheduler hermes caddy; do
  container_id="$("${compose[@]}" ps --status running -q "$service")"
  if [[ -n "$container_id" ]]; then
    previous_writer_containers+=("$container_id")
    if [[ "$service" == "worker" ]]; then
      previous_worker_running=1
    elif [[ "$service" == "scheduler" ]]; then
      previous_scheduler_running=1
    elif [[ "$service" == "hermes" ]]; then
      previous_hermes_running=1
    fi
  fi
done
previous_application_revision=""
if ((previous_boundary_valid)); then
  previous_application_revision="$previous_revision"
elif [[ -n "$previous_api_image" ]]; then
  previous_application_revision="$(
    docker image inspect \
      --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
      "$previous_api_image" 2>/dev/null || true
  )"
fi

running_image_ref() {
  local service="$1"
  local container_id configured_ref
  container_id="$("${compose[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "$service has no container while recording a release boundary" >&2
    return 1
  fi
  configured_ref="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
  if [[ ! "$configured_ref" =~ @sha256:[a-f0-9]{64}$ ]]; then
    echo "$service is not running from an immutable digest reference" >&2
    return 1
  fi
  printf '%s\n' "$configured_ref"
}

persist_release_metadata() {
  local revision="$1"
  local image_tag="$2"
  local infra_image_tag="$3"
  local api_image="$4"
  local worker_image="$5"
  local scheduler_image="$6"
  local web_image="$7"
  local caddy_image="$8"
  local postgres_image="$9"
  local redis_image="${10}"
  local backup_image="${11}"
  local hermes_image="${12}"
  local operations_revision="${13}"
  local auth_login_mode="${14}"
  local verifier_file="${15}"
  local verifier_host="${16}"
  local expires_at="${17}"
  local whatsapp_backend="${18}"
  local deployed_at revision_tmp release_tmp

  validate_release_pointer_values \
    "$revision" "$image_tag" "$infra_image_tag" \
    "$api_image" "$web_image" "$caddy_image" "$postgres_image" \
    "$backup_image" "$hermes_image" || return 1
  if [[ ! "$operations_revision" =~ ^[a-f0-9]{40}$ ]]; then
    return 1
  fi
  if [[ "$worker_image" != "$api_image" || "$scheduler_image" != "$api_image" ]] \
    || [[ ! "$redis_image" =~ ^[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}$ ]]; then
    return 1
  fi
  validate_auth_boundary_values \
    "$auth_login_mode" "$verifier_file" "$verifier_host" "$expires_at" \
    "$whatsapp_backend" || return 1

  deployed_at="$(date -u +%FT%TZ)"
  revision_tmp=".deployed-revision.tmp.$$"
  release_tmp=".deployed-release.json.tmp.$$"
  if ! printf '%s %s %s %s\n' \
    "$deployed_at" "$revision" "$image_tag" "$infra_image_tag" > "$revision_tmp"; then
    rm -f "$revision_tmp" "$release_tmp"
    return 1
  fi
  if ! jq --null-input \
    --arg deployed_at "$deployed_at" \
    --arg revision "$revision" \
    --arg operations_revision "$operations_revision" \
    --arg image_tag "$image_tag" \
    --arg infra_image_tag "$infra_image_tag" \
    --arg api "$api_image" \
    --arg worker "$worker_image" \
    --arg scheduler "$scheduler_image" \
    --arg web "$web_image" \
    --arg caddy "$caddy_image" \
    --arg postgres "$postgres_image" \
    --arg redis "$redis_image" \
    --arg backup "$backup_image" \
    --arg hermes "$hermes_image" \
    --arg auth_login_mode "$auth_login_mode" \
    --arg verifier_file "$verifier_file" \
    --arg verifier_host "$verifier_host" \
    --arg expires_at "$expires_at" \
    --arg whatsapp_backend "$whatsapp_backend" \
    '{
      deployed_at: $deployed_at,
      revision: $revision,
      operations_revision: $operations_revision,
      image_tag: $image_tag,
      infra_image_tag: $infra_image_tag,
      images: {
        api: $api,
        worker: $worker,
        scheduler: $scheduler,
        web: $web,
        caddy: $caddy,
        postgres: $postgres,
        redis: $redis,
        backup: $backup,
        hermes: $hermes
      },
      auth: {
        login_mode: $auth_login_mode,
        temporary_web_pin_verifier_file: $verifier_file,
        temporary_web_pin_verifier_file_host: $verifier_host,
        temporary_web_pin_expires_at: $expires_at,
        whatsapp_backend: $whatsapp_backend
      }
    }' > "$release_tmp"; then
    rm -f "$revision_tmp" "$release_tmp"
    return 1
  fi
  if ! chmod 0600 "$revision_tmp" "$release_tmp" \
    || ! mv "$release_tmp" .deployed-release.json \
    || ! mv "$revision_tmp" .deployed-revision; then
    rm -f "$revision_tmp" "$release_tmp"
    return 1
  fi
}

deployment_started=0
writers_quiesced=0
rollback() {
  local result=$?
  local rollback_result=1
  local predeployment_restored=1
  local hybrid_api hybrid_worker hybrid_scheduler hybrid_web hybrid_caddy
  local hybrid_postgres hybrid_redis hybrid_backup hybrid_hermes
  trap - EXIT
  if ((result == 0)); then
    return
  fi

  echo "Deployment of $deploy_commit failed." >&2
  rm -f .deployed-revision.tmp.* .deployed-release.json.tmp.*
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --no-color --tail=200 caddy api web worker scheduler hermes postgres redis >&2 || true

  if ((!deployment_started)); then
    if ((previous_boundary_valid)); then
      if restore_previous_release_boundary; then
        echo "Restored the complete previously verified release boundary." >&2
      else
        predeployment_restored=0
        echo "Unable to restore the previous release boundary; operator intervention is required." >&2
      fi
    fi
    if ! restore_previous_checkout; then
      predeployment_restored=0
      echo "Unable to restore the previous checkout; operator intervention is required." >&2
    fi
    if ((writers_quiesced && ${#previous_writer_containers[@]} > 0)); then
      echo "Restarting the previously running application after pre-deployment failure." >&2
    else
      if ((predeployment_restored)); then
        write_promotion_state "$promotion_state_file" PREVIOUS_RESTORED || \
          mark_maintenance_required "predeployment_terminal_state_write_failed" || true
      else
        mark_maintenance_required "predeployment_restore_failed" || true
      fi
      exit "$result"
    fi
    set +e
    if docker start "${previous_writer_containers[@]}" >/dev/null \
        && run_production_smoke; then
      restart_result=0
    else
      restart_result=1
    fi
    set -e
    if ((restart_result == 0)); then
      echo "The previous application resumed successfully." >&2
      if ((predeployment_restored)); then
        write_promotion_state "$promotion_state_file" PREVIOUS_RESTORED || \
          mark_maintenance_required "predeployment_terminal_state_write_failed" || true
      else
        mark_maintenance_required "predeployment_restore_failed" || true
      fi
    else
      echo "The previous application did not recover cleanly; operator intervention is required." >&2
      mark_maintenance_required "predeployment_recovery_smoke_failed" || true
    fi
  elif ((deployment_started && automatic_rollback_available)) \
    && [[ -n "$previous_api_image" && -n "$previous_web_image" ]]; then
    echo "Attempting application rollback while retaining forward-only data and edge infrastructure." >&2
    set +e
    rollback_services=(api web caddy)
    rollback_images=(api web)
    if ((previous_hermes_running)) && [[ -n "$previous_hermes_image" ]]; then
      rollback_services+=(hermes)
      rollback_images+=(hermes)
    fi
    if ((previous_worker_running && previous_scheduler_running)); then
      rollback_services+=(worker scheduler)
    fi
    # Invoked by the containment helper through its validated callback name.
    # ShellCheck cannot resolve the validated indirect callback dispatch.
    # shellcheck disable=SC2317,SC2329
    attempt_application_rollback() {
      # The containment wrapper removes Caddy and API before invoking this
      # callback. Select the complete hybrid rollback boundary before recreating
      # anything, then restore the runtime auth secret before the prior API.
      rewrite_release_boundary .env.production \
        "$previous_revision" "$previous_image_tag" "$target_infra_image_tag" \
        "$previous_api_image" "$previous_web_image" "$target_caddy_image" \
        "$target_postgres_image" "$target_backup_image" "$previous_hermes_image" \
        "$previous_auth_login_mode" "$previous_temporary_web_pin_verifier_file" \
        "$previous_temporary_web_pin_verifier_file_host" \
        "$previous_temporary_web_pin_expires_at" "$previous_whatsapp_backend" || return 1
      export API_IMAGE="$previous_api_image"
      export WEB_IMAGE="$previous_web_image"
      export HERMES_IMAGE="$previous_hermes_image"
      AUTH_LOGIN_MODE="$previous_auth_login_mode"
      TEMPORARY_WEB_PIN_VERIFIER=""
      TEMPORARY_WEB_PIN_VERIFIER_FILE="$previous_temporary_web_pin_verifier_file"
      TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST="$previous_temporary_web_pin_verifier_file_host"
      TEMPORARY_WEB_PIN_EXPIRES_AT="$previous_temporary_web_pin_expires_at"
      WHATSAPP_BACKEND="$previous_whatsapp_backend"
      export AUTH_LOGIN_MODE TEMPORARY_WEB_PIN_VERIFIER \
        TEMPORARY_WEB_PIN_VERIFIER_FILE TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST \
        TEMPORARY_WEB_PIN_EXPIRES_AT WHATSAPP_BACKEND
      if ((!previous_hermes_running)); then
        "${compose[@]}" rm -f hermes 2>/dev/null || true
      fi
      if ((!previous_worker_running || !previous_scheduler_running)); then
        "${compose[@]}" rm -f worker scheduler 2>/dev/null || true
      fi
      "${compose[@]}" pull "${rollback_images[@]}" \
        && API_IMAGE="$target_api_image" \
          "${compose[@]}" up --no-deps --force-recreate \
          --abort-on-container-exit --exit-code-from auth-secret-init auth-secret-init \
        && "${compose[@]}" up --no-deps --force-recreate \
          --abort-on-container-exit --exit-code-from caddy-init caddy-init \
        && "${compose[@]}" --profile async up -d --wait --wait-timeout 240 \
          --no-deps "${rollback_services[@]}" \
        && run_production_smoke
    }
    if run_contained_rollback_attempt \
      attempt_application_rollback mark_maintenance_required; then
      rollback_result=0
    else
      rollback_result=1
    fi
    set -e
    if ((rollback_result == 0)); then
      # The forward infrastructure has already crossed its migration boundary.
      # Record a hybrid release only after the rolled-back application passes
      # smoke, and make the environment select that same boundary on retry.
      set +e
      hybrid_api="$(running_image_ref api)"
      hybrid_worker="$(running_image_ref worker)"
      hybrid_scheduler="$(running_image_ref scheduler)"
      hybrid_web="$(running_image_ref web)"
      hybrid_caddy="$(running_image_ref caddy)"
      hybrid_postgres="$(running_image_ref postgres)"
      hybrid_redis="$(running_image_ref redis)"
      hybrid_hermes="$(running_image_ref hermes)"
      hybrid_backup="$target_backup_image"
      if [[ "$hybrid_api" == "$previous_api_image" \
        && "$hybrid_worker" == "$previous_worker_image" \
        && "$hybrid_scheduler" == "$previous_scheduler_image" \
        && "$hybrid_web" == "$previous_web_image" \
        && "$hybrid_hermes" == "$previous_hermes_image" \
        && "$hybrid_caddy" == "$target_caddy_image" \
        && "$hybrid_postgres" == "$target_postgres_image" ]]; then
        rollback_result=0
      else
        rollback_result=1
      fi
      # Persist actual-safe Compose pointers before metadata. If metadata cannot
      # be recorded, backups still use the running boundary and the next deploy
      # will fail closed on the deliberate record/live mismatch.
      if ((rollback_result == 0)) && rewrite_release_boundary .env.production \
          "$previous_revision" "$previous_image_tag" "$target_infra_image_tag" \
          "$hybrid_api" "$hybrid_web" "$hybrid_caddy" "$hybrid_postgres" \
          "$hybrid_backup" "$hybrid_hermes" \
          "$previous_auth_login_mode" "$previous_temporary_web_pin_verifier_file" \
          "$previous_temporary_web_pin_verifier_file_host" \
          "$previous_temporary_web_pin_expires_at" "$previous_whatsapp_backend" \
        && persist_release_metadata \
          "$previous_revision" "$previous_image_tag" "$target_infra_image_tag" \
          "$hybrid_api" "$hybrid_worker" "$hybrid_scheduler" "$hybrid_web" \
          "$hybrid_caddy" "$hybrid_postgres" "$hybrid_redis" \
          "$hybrid_backup" "$hybrid_hermes" "$target_revision" \
          "$previous_auth_login_mode" "$previous_temporary_web_pin_verifier_file" \
          "$previous_temporary_web_pin_verifier_file_host" \
          "$previous_temporary_web_pin_expires_at" "$previous_whatsapp_backend"; then
        rollback_result=0
      else
        rollback_result=1
      fi
      set -e
      if ((rollback_result == 0)); then
        if write_promotion_state "$promotion_state_file" HYBRID_PERSISTED; then
          echo "Application rollback succeeded and its forward-infrastructure release boundary was persisted." >&2
        else
          mark_maintenance_required "hybrid_terminal_state_write_failed" || true
          echo "Application rollback persisted but its terminal journal state failed; operator intervention is required." >&2
        fi
      else
        mark_maintenance_required "hybrid_rollback_or_persistence_failed" || true
        echo "Application rollback passed smoke but its release boundary could not be persisted; operator intervention is required." >&2
      fi
    else
      echo "Application rollback failed; public ingress and API were removed and operator intervention is required." >&2
    fi
  else
    if ((deployment_started)); then
      mark_maintenance_required "forward_boundary_without_automatic_rollback" || true
    fi
    echo "No previously verified release is available for automatic rollback." >&2
  fi
  exit "$result"
}
trap rollback EXIT

"${compose[@]}" --profile tools pull caddy postgres redis web api backup hermes

verify_image_revision() {
  local image="$1"
  local expected_revision="$2"
  local service="$3"
  local actual_revision
  actual_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
  if [[ "$actual_revision" != "$expected_revision" ]]; then
    echo "$service image revision mismatch" >&2
    exit 1
  fi
}

infra_commit="${INFRA_IMAGE_TAG#sha-}"
verify_image_revision "$API_IMAGE" "$deploy_commit" api
verify_image_revision "$WEB_IMAGE" "$deploy_commit" web
verify_image_revision "$CADDY_IMAGE" "$infra_commit" caddy
verify_image_revision "$POSTGRES_IMAGE" "$infra_commit" postgres
verify_image_revision "$BACKUP_IMAGE" "$infra_commit" backup
verify_image_revision "$HERMES_IMAGE" "$deploy_commit" hermes
./scripts/validate_temporary_auth_secret.sh \
  "$AUTH_LOGIN_MODE" "$TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" "$API_IMAGE"

target_pg_version="$(docker run --rm --entrypoint postgres "$POSTGRES_IMAGE" --version)"
target_pg_version="${target_pg_version##* }"
if [[ ! "$target_pg_version" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
  echo "Unable to parse PostgreSQL target binary version" >&2
  exit 1
fi
target_pg_major="${BASH_REMATCH[1]}"
target_pg_minor="${BASH_REMATCH[2]}"
target_pg_version_num="$((10#$target_pg_major * 10000 + 10#$target_pg_minor))"
if [[ "$target_pg_major" != "16" ]]; then
  echo "Only PostgreSQL major 16 is approved; target image reports $target_pg_major" >&2
  exit 1
fi

postgres_container="$("${compose[@]}" ps --status running -q postgres)"
writers_quiesced=1
"${compose[@]}" stop --timeout 60 caddy web api worker scheduler hermes
if [[ -n "$postgres_container" ]]; then
  running_pg_version_num="$(
    docker exec "$postgres_container" psql \
      --username "$(value_for POSTGRES_USER)" \
      --dbname "$(value_for POSTGRES_DB)" \
      --tuples-only --no-align --command 'SHOW server_version_num'
  )"
  if [[ "${running_pg_version_num:0:2}" != "$target_pg_major" ]]; then
    echo "Refusing an in-place PostgreSQL major-version change" >&2
    exit 1
  fi
  if ((10#$target_pg_version_num < 10#$running_pg_version_num)); then
    echo "Refusing to downgrade PostgreSQL from $running_pg_version_num to $target_pg_version_num" >&2
    exit 1
  fi
  legacy_brin_indexes="$(
    docker exec "$postgres_container" psql \
      --username "$(value_for POSTGRES_USER)" \
      --dbname "$(value_for POSTGRES_DB)" \
      --tuples-only --no-align \
      --command "SELECT count(*) FROM pg_indexes WHERE indexdef LIKE '%numeric_minmax_multi_ops%'"
  )"
  if [[ "$legacy_brin_indexes" != "0" ]]; then
    echo "PostgreSQL contains BRIN numeric_minmax_multi_ops indexes requiring reviewed REINDEX work" >&2
    exit 1
  fi
  non_core_extensions="$(
    docker exec "$postgres_container" psql \
      --username "$(value_for POSTGRES_USER)" \
      --dbname "$(value_for POSTGRES_DB)" \
      --tuples-only --no-align \
      --command "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname"
  )"
  if [[ -n "$non_core_extensions" ]]; then
    echo "PostgreSQL has non-core extensions requiring an explicit compatibility review" >&2
    exit 1
  fi
  echo "Creating a pre-deployment database and artifact backup."
  "${compose[@]}" --profile tools run --rm --no-deps backup-data-init
  "${compose[@]}" --profile tools run --rm --no-deps \
    --env "APPLICATION_REVISION=${previous_application_revision:-unknown}" \
    --env "BACKUP_IMAGE_REF=$BACKUP_IMAGE" \
    backup
fi

write_promotion_state "$promotion_state_file" FORWARD_BOUNDARY
deployment_started=1
"${compose[@]}" up -d --wait --wait-timeout 180 postgres redis
postgres_container="$("${compose[@]}" ps --status running -q postgres)"
docker exec \
  --env "POSTGRES_USER=$(value_for POSTGRES_USER)" \
  --env "POSTGRES_DB=$(value_for POSTGRES_DB)" \
  --env "APP_POSTGRES_PASSWORD=$(value_for APP_POSTGRES_PASSWORD)" \
  "$postgres_container" /docker-entrypoint-initdb.d/10-app-role.sh
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d --wait --wait-timeout 180 hermes
ENV_FILE=.env.production ./scripts/reconcile_hermes_profiles.sh
"${compose[@]}" --profile async up -d --wait --wait-timeout 240 --remove-orphans \
  api web worker scheduler hermes caddy
run_production_smoke

for service in caddy postgres redis api web worker scheduler hermes; do
  container_id="$("${compose[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "Required service has no container: $service" >&2
    exit 1
  fi
  state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "$container_id")"
  if [[ "$state" != "running" || "$restarts" != "0" ]]; then
    echo "$service is not stable: state=$state restarts=$restarts" >&2
    exit 1
  fi
done
for service in postgres redis api web worker scheduler hermes; do
  container_id="$("${compose[@]}" ps -q "$service")"
  health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id")"
  if [[ "$health" != "healthy" ]]; then
    echo "$service is not healthy: $health" >&2
    exit 1
  fi
done

ready_payload="$(curl --fail --silent --show-error "https://${API_DOMAIN}/health/ready")"
jq --exit-status \
  --arg whatsapp "$WHATSAPP_BACKEND" \
  --arg bumpa "$BUMPA_BACKEND" \
  --arg agent "$AGENT_BACKEND" \
  '.status == "ready" and .database == "ok" and
   .providers.whatsapp == $whatsapp and
   .providers.bumpa == $bumpa and
   .providers.agent == $agent' <<<"$ready_payload" >/dev/null

persist_release_metadata \
  "$target_revision" "$target_image_tag" "$target_infra_image_tag" \
  "$(running_image_ref api)" "$(running_image_ref worker)" \
  "$(running_image_ref scheduler)" "$(running_image_ref web)" \
  "$(running_image_ref caddy)" "$(running_image_ref postgres)" \
  "$(running_image_ref redis)" "$target_backup_image" \
  "$(running_image_ref hermes)" "$target_revision" \
  "$AUTH_LOGIN_MODE" "$TEMPORARY_WEB_PIN_VERIFIER_FILE" \
  "$TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST" "$TEMPORARY_WEB_PIN_EXPIRES_AT" \
  "$WHATSAPP_BACKEND"
rewrite_release_pointers .env.production \
  "$target_revision" "$target_image_tag" "$target_infra_image_tag" \
  "$target_api_image" "$target_web_image" "$target_caddy_image" \
  "$target_postgres_image" "$target_backup_image" "$target_hermes_image"
if ! write_promotion_state "$promotion_state_file" COMMITTED; then
  mark_maintenance_required "commit_terminal_state_write_failed" || true
  trap - EXIT
  echo "Deployment is healthy but its terminal promotion journal could not be committed" >&2
  exit 1
fi

trap - EXIT
echo "Deployed $deploy_commit with app tag $IMAGE_TAG and infrastructure tag $INFRA_IMAGE_TAG"
