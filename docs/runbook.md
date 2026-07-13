# Operations runbook

## Safety boundary

The repository's production target includes Meta WhatsApp, direct Bumpa sync,
Hermes/Claude and the durable worker/scheduler runtime. Release
`6fbe2a9eb0591bde5ad3cebe94d8f3568075df7b` is deployed on the branded
`bumpabestie.com` hosts with all eight services and selectors `meta`, `bumpa` and
`hermes` at schema `0011_tenant_onboarding`. Always confirm the actual boundary
from `.deployed-release.json`, Compose state and
`/health/ready` before applying an incident procedure; selector state is not a
provider canary.

Never:

- use local fixed OTPs, demo data or mock providers in production;
- expose Postgres, Redis, the Docker API or a Hermes port;
- paste tokens, cookies, phone numbers, raw payloads or `.env.production` into logs,
  tickets or chat;
- use `docker compose down -v` on a production host;
- replay raw production webhooks in production;
- route a tenant to a different agent profile as an outage workaround;
- report unavailable data as zero;
- run the local `make restore` convenience target for a production restore.

## Triage order

1. Establish impact: hostname, tenant pseudonym, channel, UTC start time, revision
   and correlation ID.
2. Confirm the selected provider modes and their most recent live canaries. Do not
   infer provider reachability or template approval from readiness alone.
3. Check container state, host disk/memory, listening ports and the last deployment.
4. Inspect structured logs by correlation ID, using the smallest safe time window.
5. Contain the failing boundary without bypassing tenancy, signatures, consent or
   audit requirements.
6. Preserve redacted evidence, assign an incident owner and record every manual
   mutation/replay.
7. Verify recovery with a synthetic canary, then write the follow-up and prevention
   action.

## Standard diagnostics

Run from `/opt/bumpabestie` as the `bumpabestie` user:

```bash
docker compose --env-file .env.production \
  -f compose.yaml -f compose.prod.yaml ps
docker compose --env-file .env.production \
  -f compose.yaml -f compose.prod.yaml logs --since=30m --no-color \
  caddy web api worker scheduler hermes
curl -fsS https://api.bumpabestie.com/health/live | jq
curl -fsS https://api.bumpabestie.com/health/ready | jq
cat .deployed-revision
jq . .deployed-release.json
df -h
```

`/health/live` only proves that the API process answers. `/health/ready` proves a
database query and, when enabled, Redis plus fresh worker/scheduler heartbeats. It
reports configured provider modes but is not a Meta, Bumpa or Hermes canary.
Preserve the response and image digests with any production incident evidence.

For a release, use the root-owned `/usr/local/sbin/bumpabestie-promote`
coordinator as documented in `docs/deployment.md`. It owns and inherits the
maintenance lock, snapshots the prior boundary, extracts the reviewed target
worker without first changing the checkout, and retains a durable journal until a
verified terminal state. Never invoke `scripts/promote_release.sh` or
`scripts/deploy.sh` directly and never generate a copy of the full secret-bearing
environment file. A coordinator journal, maintenance interlock or
release-record/live-container mismatch is an intentional hard stop and must be
reconciled before another deployment or backup.

The verified production snapshot has Caddy, web, API, worker, scheduler, Hermes,
PostgreSQL and Redis running; all seven services with configured healthchecks are
healthy, Caddy is running, and every service has zero restarts and zero OOM kills.
Caddy is 2.11.4 built with Go 1.26.5 and runs as UID 10001 with restricted
capabilities; PostgreSQL is 16.14 and Redis is 7.4.9. The public, `www`, API, admin
and research branded hosts have valid TLS and route correctly. Readiness reports
database/Redis/worker/scheduler `ok` and the intended provider selectors. All 23
tenant tables have ENABLE+FORCE RLS with one policy each. The non-bypass
application-role audit exercised 115 tenant/table contexts across 516 scoped rows
and found zero rows without context and zero cross-tenant rows. The onboarding audit records
five stores and exactly one approved operator/owner dual role. All five Hermes
profiles passed health and each completed an explicitly authorized live Claude
request through its mapped Hermes gateway. Forty foreign-profile gateway/control
attempts were rejected, and audited restart plus post-restart completion passed.
Bumpa remains partial: stores 1–4 return 8/10 analytics datasets; degraded store 5
returns 7/10 because
`products.overview` hit an upstream timeout/HTTP 504. Missing values remain
unavailable, not zero.

Read-only Graph checks confirm that the configured Meta test WABA and phone-number
ID pair with `+15550772716`; the sender reports `PENDING` and has five approved
non-authentication templates but zero authentication templates. Both authentication-
template create endpoints were denied with Graph code `10`/subcode `2388185`; no
outbound was sent. The lane remains reply-only with `supports_otp=false`, and
proactive outbound is disabled. These facts do not prove Meta delivery or complete
Bumpa sync.

## Field-encryption key rotation

Provider credentials and MCP OAuth state use the same versioned field-cipher
boundary. The rotation CLI enumerates durable Bumpa, Hermes and MCP credential
rows; it cannot enumerate short-lived OAuth `state` values already issued to a
browser. Never remove an old key immediately after the durable-row count reaches
zero.

Use this staged sequence. Every environment-file mutation is host-local, mode
`0600`, reviewed without printing values, and followed by the normal validation,
backup, guarded promotion and health checks.

1. **Deploy the dual reader without changing cryptographic identity.** Set
   `FIELD_ENCRYPTION_KEY_ID=primary`, `FIELD_ENCRYPTION_WRITE_VERSION=v1` and
   `FIELD_ENCRYPTION_OLD_KEYS={}`. For this first decoupling release only, initialize
   `RESEARCH_PSEUDONYM_KEY` and `ONBOARDING_INTEGRITY_KEY` from the existing
   `FIELD_ENCRYPTION_KEY` value to preserve established pseudonyms and onboarding
   idempotency hashes. Do not print or copy the value into evidence.
2. **Soak the dual-read release.** Prove credential reads, OAuth start/callback,
   Hermes and Bumpa canaries, backup and rollback. The previous v1-only release
   remains a valid rollback target because every writer still emits v1.
3. **Retire the v1-only rollback floor in a later release.** This first dual-reader
   artifact mechanically rejects `FIELD_ENCRYPTION_WRITE_VERSION=v2` in production;
   do not patch around that interlock or mutate the environment to bypass it. Ship a
   later reviewed rollback-capability release, initially with v1 writes, that proves
   the recorded previous application boundary can read both v1 and v2 before it
   permits a v2 writer to start. Only that later release may enable v2 and verify new
   credential/OAuth-state writes.
4. **Rewrap the existing key without changing its material.** Create and verify a
   production backup. Run the CLI without flags and record only its sanitized
   counts. When the dry run authenticates every row, run it with
   `--apply --confirm 'ROTATE FIELD ENCRYPTION KEYS'`. A second dry run must report
   zero `would_rotate`; do not remove any key or downgrade to a v1-only runtime.
5. **Rotate key material in a later boundary.** Move the prior material into
   `FIELD_ENCRYPTION_OLD_KEYS` under its former unique ID, generate new independent
   material for `FIELD_ENCRYPTION_KEY`, assign a new current ID, keep v2 writes,
   validate, back up and promote. Run dry-run, apply, then dry-run again exactly as
   above. Any unknown key ID or authentication failure is a hard stop.
6. **Honor the non-enumerable OAuth grace.** After the last process capable of
   issuing state under the old key has stopped, retain the old key for at least
   `MCP_OAUTH_STATE_TTL_SECONDS` plus a 300-second deployment/clock-skew margin.
   Exercise an OAuth callback created before rotation while the old ring is present.
   Only after the grace, zero-old durable verification and a fresh backup may the
   old key be removed in a new guarded promotion.

The CLI output intentionally contains counts and key IDs only. Never capture the
environment, decrypted credentials or OAuth state in a transcript. A rollback
after phase 3 targets a dual-reader release with the matching old-key ring; the
original v1-only release is no longer valid.

## Historical provider-disabled baseline verification

Expected running application services are Caddy, web, API, Postgres and Redis.
Worker and scheduler must be absent, and no Hermes service/port should exist.
Readiness must report all providers as `disabled`.

Verify the public boundaries:

```bash
SMOKE_SCHEME=https SMOKE_PORT=443 SMOKE_OVERALL_TIMEOUT_SECONDS=60 \
  ./scripts/smoke_test.sh
```

`SMOKE_OVERALL_TIMEOUT_SECONDS` is one positive deadline shared by every
endpoint and retry. To verify the local TLS origin without trusting public DNS or
an inherited proxy, set `SMOKE_ORIGIN_ADDRESS=127.0.0.1`; curl still validates
the normal hostname certificate and SAN through a per-host `--resolve` mapping.

Then verify the principal public provider-dependent path fails closed:

```bash
status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  -H 'Content-Type: application/json' \
  --data '{"phone_e164":"+2348000000000"}' \
  https://api.bumpabestie.com/v1/auth/request-otp)"
test "$status" = 503
```

The command should fail with HTTP 503. Use a reserved synthetic number, not a real
person's phone. Other protected provider paths require an authenticated synthetic
operator and should be negative-canary tested only after a safe bootstrap mechanism
exists. Any local OTP, fake sync or fake agent response in production is a critical
release failure.

## Backups

### What the current backup contains

The backup container creates a UTC-named directory in the `backups_data` volume
containing:

- `postgres.dump`, a custom-format dump with no ownership/privilege statements;
- `exports.tar.gz` when the exports source exists;
- `hermes-runtime.tar.gz` for provisioned profiles and durable runtime state;
- `hermes-staging.tar.gz` for the control-plane profile handoff volume;
- `manifest.json` (format 3, including server/dump versions, migration revision,
  application revision, backup image tag and exact backup image reference); and
- `SHA256SUMS`.

Retention deletes local backup directories older than `BACKUP_RETENTION_DAYS`.
This is not an off-host durability mechanism.

### Create and verify a production local backup

```bash
compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)
"${compose[@]}" --profile tools run --rm --no-deps backup
"${compose[@]}" --profile tools run --rm --no-deps --entrypoint sh backup -c \
  'latest=$(find /backups -mindepth 1 -maxdepth 1 -type d | sort | tail -1); test -n "$latest"; cd "$latest"; sha256sum -c SHA256SUMS; cat manifest.json'
```

Record the backup directory name, UTC time, revision, database name, server/dump
versions, schema revision, backup image tag and exact digest reference, checksum
result, duration and operator.
The manifest lists the Hermes and exports components by contract. Verify the Hermes
archive inventory and permissions after restore; archive presence alone does not
prove that a tenant profile is healthy.

`caddy_data` and `caddy_config` are not included. Caddy can reissue certificates,
but loss also discards its ACME account/state and can encounter issuer rate limits.
Before real traffic, add an encrypted off-host volume snapshot or a separately
reviewed Caddy-state backup and prove recovery.

### Off-host handoff

The repository does not choose or credential an off-host provider. If
`OFFSITE_BACKUP_SCRIPT` is unset, `scripts/offsite_backup.sh` warns and exits zero.
The systemd unit passes `.env.production` to a narrow parser that reads only this
literal executable path without sourcing/exporting the file. The operator-owned
script still needs an explicitly reviewed credential boundary and a tested
latest-backup input before use.
Consequently:

- systemd success proves the local backup stage only;
- an operator-owned handoff must explicitly identify the newest completed backup;
- encryption, transport, retention and deletion must be owned by that handoff;
- success evidence must include a remote object/snapshot ID and matching checksum;
- an alert must distinguish local-backup failure from off-host-copy failure.

Do not call the backup durable until an isolated machine has restored from the
remote object. Keep off-host credentials outside git and outside command output.

### Timer operations

```bash
sudo systemctl start bumpabestie-backup.service
sudo systemctl enable --now bumpabestie-backup.timer
systemctl status bumpabestie-backup.timer --no-pager
systemctl list-timers bumpabestie-backup.timer
journalctl -u bumpabestie-backup.service --since '2 days ago' --no-pager
```

The timer runs at 02:30 UTC with up to 15 minutes randomized delay and is persistent
across downtime. The scheduled wrapper records the running application services,
keeps Caddy/web serving, quiesces API/worker/scheduler/Hermes, creates the backup,
and resumes exactly the recorded service set even when backup creation fails. Alert
if no verified backup ID appears within the expected window or any service fails to
resume.

Post-release backup `20260713T184042Z` was created at 18:40:52 UTC and passed its
format-3 manifest and all five SHA-256 entries. Its manifest records application
revision `6fbe2a9eb0591bde5ad3cebe94d8f3568075df7b`, schema
`0011_tenant_onboarding`, PostgreSQL dump/server 16.14 and backup image
`ghcr.io/makriman/bumpabestie-backup@sha256:9ef16f2273b422f603483f1d88c3d6195267cd04aa4fbadd3288104c543c70c1`.
Systemd completed successfully at 18:41:11 UTC; all eight services resumed, all
seven configured healthchecks passed, restarts/OOM kills remained zero, readiness
passed and all five public routes were correct. Backup and disk-usage timers are
active. A 10m35s stability audit through 18:49:27 found no unhealthy service,
restart, OOM kill or severe-log match. Off-host durability remains unconfigured.

## Restore drill

### Important behavior

Restore is destructive: the guarded restore resets the supported `public` schema,
restores its database objects, reapplies the restricted application-role grants,
and clears the exports/Hermes volume contents before archive extraction.
`BACKUP_PATH` is a path **inside the backup container**, normally
`/backups/<UTC-backup-id>`; a host path is not automatically mounted there.

For production, use the exact production Compose files below. Do not use
`make restore`, which is a local convenience target.

### Preflight

1. Declare a maintenance window and incident/drill owner.
2. Record current revision, image digests, schema revision, service state and safe
   row counts.
3. Create and verify a pre-restore backup of the current state.
4. If testing off-host durability, download the remote object to protected staging,
   verify its transport checksum and copy the complete backup directory into the
   `backups_data` volume. Record how it was staged.
5. Verify `manifest.json`, `SHA256SUMS`, `postgres.dump` and expected archives.
6. Confirm free space for the dump, extracted data and rollback material.
7. Confirm the restore PostgreSQL image has the same major as the manifest and the
   backup client is not older than the recorded server.
8. Stop Caddy, web, API, worker, scheduler and Hermes before replacing database or
   profile state.

### Restore command

```bash
compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)
backup_id=YYYYMMDDTHHMMSSZ

"${compose[@]}" stop caddy web api worker scheduler hermes
"${compose[@]}" --profile restore run --rm --no-deps \
  -e RESTORE_CONFIRM=restore-bumpabestie \
  -e BACKUP_PATH="/backups/$backup_id" \
  restore
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d --wait postgres redis hermes
ENV_FILE=.env.production ./scripts/reconcile_hermes_profiles.sh
"${compose[@]}" --profile async up -d --wait api web worker scheduler hermes caddy
SMOKE_SCHEME=https SMOKE_PORT=443 SMOKE_OVERALL_TIMEOUT_SECONDS=60 \
  ./scripts/smoke_test.sh
```

If restore fails, keep writers stopped, preserve logs and the pre-restore backup,
and investigate before another destructive attempt. Do not improvise a partial
tenant restore with this whole-database script.

### Acceptance and evidence

For a provider-disabled containment release, validate checksums, migration head, expected
database counts, artifact checksums, service health, domain routing and disabled
provider state. This proves baseline recovery only.

The build plan's full restore acceptance remains open until an authenticated
synthetic admin/researcher can load historical data and a known tenant can reach its
restored Hermes profile. Also compare profile file inventory,
permissions, state checksum and a cross-profile isolation canary. Record recovery
time, recovery point, backup ID, source (local or off-host), revision and reviewer in
`docs/verification.md` or the protected evidence system.

## Add a new SME

### Activation guard

Do not onboard a production SME while any required provider for that SME is
disabled or lacks its activation evidence. Creating only tenant rows would leave a
misleading and unusable account.

Local synthetic onboarding is covered by backend tests and `make integration`; it
is not a substitute for production onboarding evidence.

### Onboarding procedure

Use this only after Meta, Bumpa, Hermes/Claude, the queue, and production auth have
each passed their activation gate for the exact release:

1. Authenticate as an operator/superadmin and record a change ticket.
2. Create the tenant with canonical slug, timezone, currency and explicit research
   consent state. Record tenant and audit IDs.
3. Create the owner identity and membership. Verify that no duplicate phone can map
   across tenants.
4. Add and approve the owner's WhatsApp number; verify the intended tenant mapping
   without logging the raw number.
5. Write the Bumpa key once, with explicit `scope_type` and `scope_id`; never display
   the decrypted key afterward.
6. Run a bounded test sync. Confirm all expected datasets/orders, availability,
   Decimal normalization, rate-limit metadata and freshness. Do not proceed on an
   auth error or unexplained partial result.
7. Provision and activate the tenant's Hermes profile. The admin request must not
   return `active` until the private control plane has imported the exact staged
   bundle, started the selected gateway and passed authenticated readiness. Verify
   private URL/port/auth, filesystem isolation, health, SOUL/policy and absence of
   Bumpa/Meta keys in its context. A degraded result or activation-failure audit is
   a stop condition; retry the same profile rather than creating another mapping.
8. Send the approved invite/OTP and complete owner login.
9. Run one synthetic browser chat and one approved WhatsApp canary; verify both hit
   the same tenant profile and that another tenant cannot access either record.
10. Verify the consent decision and the expected redacted research event. Do not
    classify research data before consent.
11. Run a backup and record the tenant/audit/sync/profile/message/research evidence
    IDs. Remove synthetic canary content according to retention policy.

Any missing audit record, ambiguous number, unavailable sync, profile isolation
failure or raw PII/secret in telemetry is a stop condition.

## Bumpa failure

If readiness reports `BUMPA_BACKEND=disabled`, sync must be unavailable; treat a
fabricated success as a critical configuration defect. When the selector is
`bumpa`, use the procedure below.

The current provider baseline is partial, not recovered: stores 1–4 return 8/10
analytics datasets and degraded store 5 returns 7/10, with `products.overview`
failing at the upstream boundary by timeout/HTTP 504. Do not close a Bumpa incident
or enable freshness-dependent behavior until the expected 10/10 dataset surface is
restored and a redacted canonical/raw count reconciliation succeeds.

1. Establish affected tenant, date range, sync run and correlation ID.
2. Inspect HTTP status, body-level error, pagination checkpoint and rate-limit
   metadata without exposing the key or raw PII.
3. Keep unavailable metrics unavailable; do not substitute zero or stale values
   without marking freshness.
4. Retry only through the idempotent bounded policy. Do not increase concurrency or
   manually loop on 429/5xx.
5. For 401/403, suspend the connection and rotate the tenant key through the
   write-only path.
6. Reconcile raw/canonical counts after recovery and verify a safe business-context
   canary.
7. Record alert, retry and final sync-run IDs. Escalate contract drift to Bumpa with
   a redacted fixture.

## WhatsApp/Meta failure

If readiness reports `WHATSAPP_BACKEND=disabled`, callback verification, inbound
processing and OTP delivery must be unavailable; do not point Meta at that state or
tell users an OTP was sent. When the selector is `meta`, use the procedure below.

For the test lane, read-only Graph checks confirm the configured WABA/phone-number
pair and a non-empty subscription list. The sender reports `PENDING`, five approved
non-authentication templates and zero authentication templates; both auth-template
create endpoints return code
`10`/subcode `2388185`. This is a provider permission
block: do not retry in a loop, claim OTP support, switch on proactive sends or use
the reply-only sender as a production authentication substitute.

1. Determine whether failure is callback verification, signature rejection, queue
   lag, sender/template error or delivery failure.
2. Check Graph version, token expiry/permissions, WABA/phone IDs, template status,
   service window, opt-out state and provider request ID without logging secrets.
3. Never bypass signature verification or dedupe to restore traffic.
4. Pause proactive sends on broad failure; preserve inbound durable events for
   bounded processing once recovered.
5. Reprocess only by claimed event/idempotency ID. Never replay a raw customer
   payload from a ticket.
6. Verify an approved synthetic recipient, delivery callback, STOP/START and tenant
   routing before resuming.

## Hermes/Claude failure

Operator-initiated profile recovery is available in the admin tenant view. It
requires an explicit confirmation and controlled reason, is audit logged, and
restarts only the selected profile through the authenticated internal control
plane. Initial onboarding uses the same private listener for one narrower
activation operation: it reads an API-staged bundle from a read-only volume,
accepts only the required regular policy files, refuses symlinks, special files and
runtime key/port/policy mismatches, atomically creates that named runtime profile,
runs the fixed gateway start command and waits for authenticated readiness. The
control listener is not host-published, cannot accept an arbitrary profile path or
command, and has no Docker socket, host mount or root identity. Use a full
service/container restart only when the profile-scoped control plane itself is
unhealthy.

All five mapped profiles have completed one live Claude request through Hermes. A
failure to complete now is a regression from that baseline. Forty foreign-profile
gateway/control attempts were rejected, and audited restart plus post-restart
completion passed; these do not replace backup/restore or WhatsApp-routing canaries.

1. Circuit-break only the affected profile and return a clear unavailable response.
2. Verify tenant/profile mapping, private endpoint, authentication, process health,
   model availability, budget/limit state and redacted context size.
3. Never route to another tenant profile and never inject an Anthropic key into the
   control-plane services.
4. Use only the approved profile activation or restart operation; record actor,
   reason and audit ID.
5. If state is corrupt, restore only from a verified backup with the profile mapping
   preserved.
6. Run same-tenant functionality and cross-profile isolation canaries before
   reopening traffic.

## Queue or scheduler failure

Postgres jobs/outbox are authoritative; Redis carries wake-up IDs and heartbeats.
Never replay a job without checking its idempotency key and
side-effect record. Redis recovery alone does not prove Postgres-to-queue handoff
correctness.

## Proactive insight and external alert controls

- Keep `PROACTIVE_INSIGHTS_ENABLED=false` until both Meta templates are approved.
  After activation, prove one synthetic owner delivery per cadence, STOP blocks the
  next slot and START does not replay an old slot.
- `ineligible`, `no_recipients` and `no_fresh_data` are intentional no-send results.
  Correct the source state; never bypass a calendar/message idempotency fence.
- Pause the master flag on broad Meta/template errors. An `ambiguous` outbound row
  requires Meta reconciliation before replay.
- App alerts cover degraded/terminal Bumpa sync, outbound WhatsApp failure, Hermes
  call errors and periodic Hermes health. Host alerts cover disk/inode pressure and
  backup success/failure.
- If alerts stop, check worker dead letters, both systemd unit journals, endpoint
  TLS, secret-file ownership/mode and receiver HMAC verification. Never print the
  secret or copy customer/provider payloads into incident tools.
- Receivers must accept duplicate idempotency keys after transport retries.
  Non-retryable 4xx responses become terminal; 408/425/429/5xx and transport
  failures use the bounded retry budget.

## Database, disk or container failure

- On database failure, stop writers, preserve logs, check storage and Postgres
  integrity, then follow the restore procedure if required.
- On disk-near-full, stop writers before deleting anything. Preserve the newest
  verified backup and current logs; prune only identified caches/old images/backups
  under an approved retention decision.
- After a container restart, verify revision/image digest, migration head,
  idempotency state, domain routes and the current provider modes.
- At migration `0008_bumpa_dataset_failures`, a hybrid application rollback is
  intentionally schema-forward: retain the target operations checkout and do not
  down-migrate. Pre-0008 writers can still record HTTP responses, while current
  writers use nullable HTTP status only with typed `timeout` or `transport`
  evidence. A 0008 downgrade must stop if such status-less evidence exists.
- Sync publication uses a connection row lock plus a nested savepoint, so a failed
  publication records its terminal audit before a queued sync starts. If two
  consecutive outer database commits both fail ambiguously while recovery commits
  immediately become available, canonical data and run evidence still fail closed
  and both failed audits are retained; the cached `last_failed_sync_at` may be the
  older timestamp. Reconcile that cache from the newest terminal
  `bumpa_sync_runs.finished_at` during the incident review.
- Restarting Redis can discard wake-ups and heartbeats, but not authoritative jobs;
  verify scheduler redispatch and both worker/scheduler heartbeats after recovery.

## Suspected secret or PII exposure

1. Stop further distribution and disable the affected credential/path.
2. Preserve access evidence without copying the sensitive value.
3. Rotate every affected credential, including derived sessions where relevant.
4. Identify tenants, data classes, environments and time window.
5. Remove public artifacts and follow the incident/legal notification policy.
6. Verify history scans, logs, exports and backups; deletion from the working tree is
   not remediation by itself.

Do not rewrite repository history or delete logs during initial containment without
an approved forensic and coordination plan.
