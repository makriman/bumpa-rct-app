# Operations runbook

## Safety boundary

The current production target is an infrastructure-only baseline. Meta WhatsApp,
Bumpa and Hermes/Claude are disabled; worker and scheduler are not production
services yet. An incident procedure below marked **future activation** is a required
procedure for the eventual adapter, not proof that the adapter exists.

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
2. Confirm whether this is the provider-disabled baseline or a later approved live
   mode. Do not assume a provider is enabled.
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
  -f compose.yaml -f compose.prod.yaml logs --since=30m --no-color caddy web api
curl -fsS https://api.bumpabestie.com/health/live | jq
curl -fsS https://api.bumpabestie.com/health/ready | jq
cat .deployed-revision
df -h
```

`/health/live` only proves that the API process answers. `/health/ready` currently
proves a database query and reports configured provider modes. It is not a Meta,
Bumpa, Redis or Hermes canary. Preserve the response and image digests with any
production incident evidence.

## Provider-disabled baseline verification

Expected running application services are Caddy, web, API, Postgres and Redis.
Worker and scheduler must be absent, and no Hermes service/port should exist.
Readiness must report all providers as `disabled`.

Verify the public boundaries:

```bash
SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh
```

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
- `hermes.tar.gz` for the reserved Hermes volume, which is empty until Hermes is
  implemented;
- `manifest.json`; and
- `SHA256SUMS`.

Retention deletes local backup directories older than `BACKUP_RETENTION_DAYS`.
This is not an off-host durability mechanism.

### Create and verify a production local backup

```bash
compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)
"${compose[@]}" --profile tools run --rm backup
"${compose[@]}" --profile tools run --rm --entrypoint sh backup -c \
  'latest=$(find /backups -mindepth 1 -maxdepth 1 -type d | sort | tail -1); test -n "$latest"; cd "$latest"; sha256sum -c SHA256SUMS; cat manifest.json'
```

Record the backup directory name, UTC time, revision, database name, checksum
result, duration and operator. The manifest currently lists the reserved Hermes and
exports components by contract; inspect archive presence/size rather than assuming
they contain usable runtime state.

### Off-host handoff

The repository does not currently choose or credential an off-host provider. If
`OFFSITE_BACKUP_SCRIPT` is unset, `scripts/offsite_backup.sh` warns and exits zero.
The current systemd unit also does not load that variable from `.env.production`,
so adding it to the file alone does not configure a handoff. The unit and the
operator-owned script need an explicitly reviewed credential/environment boundary
and a tested latest-backup input before use.
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
across downtime. Alert if no verified backup ID appears within the expected window.

## Restore drill

### Important behavior

Restore is destructive: `pg_restore --clean --if-exists` replaces database objects,
and the exports/Hermes volume contents are cleared before archive extraction.
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
7. Stop all writers. In the current baseline that means Caddy, web and API; future
   workers/scheduler/Hermes must also be stopped.

### Restore command

```bash
compose=(docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml)
backup_id=YYYYMMDDTHHMMSSZ

"${compose[@]}" stop caddy web api
"${compose[@]}" --profile tools run --rm \
  -e RESTORE_CONFIRM=restore-bumpabestie \
  -e BACKUP_PATH="/backups/$backup_id" \
  --entrypoint /usr/local/bin/restore.sh backup
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d caddy postgres redis api web
SMOKE_SCHEME=https SMOKE_PORT=443 ./scripts/smoke_test.sh
```

If restore fails, keep writers stopped, preserve logs and the pre-restore backup,
and investigate before another destructive attempt. Do not improvise a partial
tenant restore with this whole-database script.

### Acceptance and evidence

For the provider-disabled baseline, validate checksums, migration head, expected
database counts, artifact checksums, service health, domain routing and disabled
provider state. This proves baseline recovery only.

The build plan's full restore acceptance remains open until an authenticated
synthetic admin/researcher can load historical data and a known tenant can reach its
restored Hermes profile. After Hermes exists, also compare profile file inventory,
permissions, state checksum and a cross-profile isolation canary. Record recovery
time, recovery point, backup ID, source (local or off-host), revision and reviewer in
`docs/verification.md` or the protected evidence system.

## Add a new SME

### Current production state

Do not onboard a production SME while any required provider is disabled. The
current baseline cannot deliver OTP, perform a live Bumpa sync, provision a real
Hermes profile or complete first chat. Creating only tenant rows would leave a
misleading and unusable account.

Local synthetic onboarding is covered by backend tests and `make integration`; it
is not a substitute for production onboarding evidence.

### Future activation procedure

Use this only after Meta, Bumpa, Hermes/Claude, the queue and production auth have
each passed their activation gate:

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
7. Provision the tenant's Hermes profile. Verify private URL/port/auth, filesystem
   isolation, health, SOUL/policy and absence of Bumpa/Meta keys in its context.
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

### Current baseline

`BUMPA_BACKEND=disabled`; sync should be unavailable. There is no live Bumpa client
to retry or credential to rotate. Treat a fabricated successful sync as a critical
configuration defect.

### Future activation

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

### Current baseline

`WHATSAPP_BACKEND=disabled`; callback verification, inbound processing and OTP
delivery should be unavailable in production. Do not point a Meta callback at this
baseline and do not tell users an OTP was sent.

### Future activation

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

### Current baseline

`AGENT_BACKEND=disabled`; there is no Hermes container, profile process, restart API
or production agent client. A local database profile record is not a real profile.
Do not restart a nonexistent service or route traffic to the local agent mock.

### Future activation

1. Circuit-break only the affected profile and return a clear unavailable response.
2. Verify tenant/profile mapping, private endpoint, authentication, process health,
   model availability, budget/limit state and redacted context size.
3. Never route to another tenant profile and never inject an Anthropic key into the
   control-plane services.
4. Use the approved lifecycle operation for restart; record actor, reason and audit
   ID.
5. If state is corrupt, restore only from a verified backup with the profile mapping
   preserved.
6. Run same-tenant functionality and cross-profile isolation canaries before
   reopening traffic.

## Queue or scheduler failure

Production worker/scheduler procedures become active only after the Redis queue,
transactional handoff/outbox, bounded retries, dead-letter visibility and health
metrics are implemented. Today, worker and scheduler are idle local shells and are
intentionally removed by production deployment. If either is running in the
provider-disabled baseline, stop it and treat that as configuration drift.

After activation, never replay a job without checking its idempotency key and
side-effect record. Redis recovery alone does not prove Postgres-to-queue handoff
correctness.

## Database, disk or container failure

- On database failure, stop writers, preserve logs, check storage and Postgres
  integrity, then follow the restore procedure if required.
- On disk-near-full, stop writers before deleting anything. Preserve the newest
  verified backup and current logs; prune only identified caches/old images/backups
  under an approved retention decision.
- After a container restart, verify revision/image digest, migration head,
  idempotency state, domain routes and the current provider modes.
- Redis is not yet a production queue/rate-limit source of truth. Its health is
  still useful infrastructure evidence, but restarting it cannot lose an assumed
  job queue because that queue is not implemented.

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
