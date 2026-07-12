#!/usr/bin/env bash
set -Eeuo pipefail

base="${E2E_BASE_URL:-http://bumpabestie.localhost:8080}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

wait_for_stack() {
  local attempt
  for attempt in {1..30}; do
    if curl -fsS --max-time 3 "$base/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Local stack did not become ready at $base within 30 seconds" >&2
  return 1
}

login() {
  local phone="$1"
  local jar="$2"
  curl -fsS -c "$jar" \
    -H 'Content-Type: application/json' \
    -d "{\"phone_e164\":\"$phone\"}" \
    "$base/api/backend/auth/request-otp" >/dev/null
  curl -fsS -b "$jar" -c "$jar" \
    -H 'Content-Type: application/json' \
    -d "{\"phone_e164\":\"$phone\",\"code\":\"246810\"}" \
    "$base/api/backend/auth/verify-otp" >/dev/null
}

wait_for_stack

owner_jar="$work_dir/owner.cookies"
login "+2348012345678" "$owner_jar"
owner_name="$(curl -fsS -b "$owner_jar" "$base/api/backend/auth/me" | jq -r '.user.name')"
[[ "$owner_name" == "Ada Owner" ]]

sync_status="$(curl -fsS -b "$owner_jar" \
  -H 'Content-Type: application/json' \
  -d '{"date_from":"2026-06-12","date_to":"2026-07-12"}' \
  "$base/api/backend/bumpa/sync" | jq -r '.status')"
[[ "$sync_status" == "success" ]]

chat_answer="$(curl -fsS -b "$owner_jar" \
  -H 'Content-Type: application/json' \
  -d '{"message":"What sold best and how are sales?","client_message_id":"local-e2e-owner-1"}' \
  "$base/api/backend/chat/web" | jq -r '.answer')"
[[ "$chat_answer" == *"Sales:"* ]]

researcher_jar="$work_dir/researcher.cookies"
login "+2348099990002" "$researcher_jar"
event_count="$(curl -fsS -b "$researcher_jar" \
  "$base/api/backend/research/overview" | jq -r '.research_events')"
((event_count >= 1))

report="$(curl -fsS -b "$researcher_jar" \
  -H 'Content-Type: application/json' \
  -d '{"report_type":"question_taxonomy","formats":["csv","jsonl","pdf"]}' \
  "$base/api/backend/research/reports")"
report_id="$(jq -r '.id' <<<"$report")"
[[ "$(jq -r '.status' <<<"$report")" == "success" ]]
curl -fsS -b "$researcher_jar" \
  -o "$work_dir/report.pdf" \
  "$base/api/backend/research/reports/$report_id/download/pdf"
head -c 8 "$work_dir/report.pdf" | grep -q '%PDF-1.4'

echo "PASS local integration: OTP, tenant session, Bumpa sync, chat, research event, and PDF report"
