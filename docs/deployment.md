# Deployment

## Current release boundary

The deployable target is a six-image production stack: API, web, Caddy, PostgreSQL,
backup and the pinned Hermes runtime. Redis remains an upstream digest-pinned
service. Worker and scheduler run from the API image and use Postgres jobs/outbox as
the source of truth with Redis wake-ups and heartbeats. Production always requires
`ASYNC_RUNTIME_ENABLED=true`; provider selectors may be disabled for containment or
set to `meta`, `bumpa` and `hermes` only after their activation gates pass.

As of 2026-07-13, release
`8f290509668de15eaf3621e3213f4276f85a0a83` is live on the five branded
`bumpabestie.com` hosts with valid TLS. [PR 26](https://github.com/makriman/bumpa-rct-app/pull/26),
[main CI 29246406311](https://github.com/makriman/bumpa-rct-app/actions/runs/29246406311)
and [publish run 29247014725](https://github.com/makriman/bumpa-rct-app/actions/runs/29247014725)
are its exact-revision release gates. Deployed image index references are recorded
in `docs/verification.md`.

Production verification found all eight services healthy with zero restarts or OOM
kills. Readiness requires PostgreSQL, Redis and fresh worker/scheduler heartbeats
and reports provider selectors `meta`, `bumpa` and `hermes`. Meta's callback
challenge and signed-ingress processing are live, but business verification and
approved production templates still block OTP/outbound traffic. Recovery-point
backup `20260713T121212Z` passed its format-3 manifest, five checksums and all
archive/dump parsers at schema `0008_bumpa_dataset_failures`; the nightly timer is
enabled. Provider selectors and valid credentials do not by themselves authorize
real tenant traffic.

Do not change a provider selector from `disabled` merely because a credential has
been obtained. Use the activation gates in `docs/build-plan-compliance.md`.

## External prerequisites

The current baseline already has DigitalOcean access, an authorized non-root SSH
deploy key and branded DNS/TLS. A fresh host or future promotion still requires:

1. Published immutable API/web/Caddy/PostgreSQL/backup/Hermes images and either public
   GHCR packages or a least-privilege host credential with `read:packages` only.
2. An encrypted off-host backup target and its host-only credential.
3. An alert destination and an identified operator for deployment and restore.

Meta and Anthropic credentials are host secret files under `SECRETS_DIR`; Bumpa keys
are encrypted per tenant in Postgres. Claude is called through Hermes for the
SME-agent path, and its Anthropic key is mounted only into Hermes—never the shared
application environment.

## Host prerequisites

- Ubuntu 24.04 LTS on `linux/amd64`, a stable IP, provider backups and monitoring.
- Cloud firewall: public TCP 80/443; SSH 22 only from a trusted `/32` or VPN CIDR.
- SSH keys only, non-root `bumpabestie` deploy user and `/opt/bumpabestie` mode 0750.
- No public Postgres, Redis, Docker API or Hermes ports.
- Enough free disk for current data, image rollback, one local backup and restore
  staging without approaching a disk-full condition.

On a fresh supported host, review the script and run it from the checked-out
revision:

```bash
ADMIN_SSH_CIDR=203.0.113.10/32 sudo --preserve-env=ADMIN_SSH_CIDR ./scripts/bootstrap_server.sh
```

Record the OS release, Docker/Compose versions, firewall rules, listening sockets
and the deploy user's SSH fingerprint. The bootstrap script installs and enables
the backup timer when the unit files are present. When the script is streamed to a
fresh host before checkout, install the units from the checked-out release after
the production environment and first manual backup are ready.

## DNS

Create these records at the authoritative DNS provider, all pointing to the stable
Droplet IP:

```text
A  bumpabestie.com
A  www.bumpabestie.com
A  api.bumpabestie.com
A  admin.bumpabestie.com
A  research.bumpabestie.com
```

Remove conflicting A/AAAA/CNAME records. If IPv6 is not configured on the host and
firewall, do not publish AAAA records. Before deployment, verify each hostname from
an external resolver. Caddy cannot obtain public certificates until resolution and
ports 80/443 are correct.

For an infrastructure-only baseline while the branded domain is unavailable, a
temporary wildcard DNS service such as sslip.io may point five dedicated preview
hosts at the Droplet. Record those names explicitly, obtain valid certificates, and
keep providers disabled. A temporary hostname is not a substitute for registering
and controlling the launch domain and must never receive real SME traffic.

## Production environment

Create `/opt/bumpabestie/.env.production` directly on the server with mode `0600`.
Start from `.env.example` as a list of names, not as a value source. Do not copy a
local `.env`.

Full provider launch mode uses the following controls. During a documented
containment rollout, any provider selector may remain `disabled`; production mock
selectors are always forbidden.

```bash
APP_ENV=production
SESSION_COOKIE_SECURE=true
EXPOSE_LOCAL_OTP=false
SEED_DEMO_DATA=false
CADDY_SITE_SCHEME=https
CADDY_BIND_ADDRESS=0.0.0.0
WHATSAPP_BACKEND=meta
BUMPA_BACKEND=bumpa
AGENT_BACKEND=hermes
ASYNC_RUNTIME_ENABLED=true
SECRETS_DIR=/opt/bumpabestie/secrets
```

Do not include `DEV_FIXED_OTP` or `DEV_OTP_SINK`. Set the five production domains
and HTTPS origins, independent high-entropy application/database secrets, internal
database URLs, `GHCR_OWNER`, immutable `DEPLOY_REF`,
`IMAGE_TAG=sha-<full-commit-sha>` and a separately promoted
`INFRA_IMAGE_TAG=sha-<full-infrastructure-commit-sha>`. `latest` is forbidden.
Set `API_IMAGE`, `WEB_IMAGE`, `CADDY_IMAGE`, `POSTGRES_IMAGE`, `BACKUP_IMAGE` and `HERMES_IMAGE`
to exact `ghcr.io/<owner>/<repository>@sha256:<64-hex-digest>` references. Tags
identify releases; production Compose consumes digests.

```dotenv
API_IMAGE=ghcr.io/<owner>/bumpabestie-api@sha256:<index-digest>
WEB_IMAGE=ghcr.io/<owner>/bumpabestie-web@sha256:<index-digest>
CADDY_IMAGE=ghcr.io/<owner>/bumpabestie-caddy@sha256:<index-digest>
POSTGRES_IMAGE=ghcr.io/<owner>/bumpabestie-postgres@sha256:<index-digest>
BACKUP_IMAGE=ghcr.io/<owner>/bumpabestie-backup@sha256:<index-digest>
HERMES_IMAGE=ghcr.io/<owner>/bumpabestie-hermes@sha256:<index-digest>
```

`ANTHROPIC_API_KEY` does not belong in the shared API/web/worker/scheduler
environment. `hermes_anthropic_api_key` is a `0600` file in the `0700` secrets
directory. Per-tenant Bumpa keys do not belong in this file; the onboarding/admin
workflow stores them encrypted in Postgres.

Hermes lifecycle operations use an internal-only control listener on
`HERMES_CONTROL_PORT` (default `8699`). The port must sit outside the per-profile
gateway range and is never published by Compose. For initial activation, the API
stages one private allowlisted profile bundle, then authenticates with that
profile's encrypted gateway key. The unprivileged control service reads the
staging volume read-only, rejects unexpected entries, symlinks, special files and
mismatched existing runtime profiles, atomically installs only the required policy
files, runs the fixed `hermes -p <profile> gateway start` command and waits for the
authenticated profile readiness endpoint. Recovery accepts only the corresponding
fixed gateway restart command. The listener accepts no caller-supplied path or
command and has no Docker socket, host mount, root identity or host privileges.

Validate without printing values:

```bash
chmod 0600 .env.production
./scripts/validate_env.sh .env.production production
docker compose --env-file .env.production \
  -f compose.yaml -f compose.prod.yaml config --quiet
```

Inspect the rendered service list, networks, images and published ports. Never paste
the rendered environment into a ticket or evidence artifact.

## Image release

Pull-request CI builds `linux/amd64` API, web, Caddy, PostgreSQL, backup and Hermes images
without publishing them. It also boots the infrastructure images, adopts a 16.9
data volume with 16.14, creates a backup and restores it into an isolated database.
`.github/workflows/publish-images.yml` publishes all six images to GHCR after it
finds a successful CI run for the exact commit. The deployable tag is
`sha-<full-commit-sha>`; a release tag is an alias, and `latest` is never emitted.
The build requests provenance and SBOM attestations. CI scans every locally built
runtime, and publication scans each exact registry digest for fixable critical/high
findings; JSON reports are retained as workflow artifacts.

Before deploy, put the exact published index digests in the six image-reference
settings, record the platform manifests and scan those exact images. A successful
build or SBOM is not a vulnerability scan. If GHCR packages remain
private, log in on the host using a dedicated pull-only credential and ensure the
credential file is owned by the deploy user with restrictive permissions.

The infrastructure tag is intentionally independent from the application tag.
Routine application releases keep the promoted Caddy/PostgreSQL/backup references
unchanged. Changing `POSTGRES_IMAGE` is a data-plane operation, even within major
16, and requires the compatibility checks and backup performed by the deploy
script.

Caddy runs as fixed UID/GID `10001` with a read-only root filesystem and only
`NET_BIND_SERVICE`. A one-shot, networkless initializer owns the persistent Caddy
volumes before the edge process starts. The PostgreSQL server and routine backup
work retain the official UID/GID 70 boundary. The destructive restore profile runs
only on operator request as root with a separate narrow capability set, including
`DAC_OVERRIDE`, so it can replace stale restricted artifact contents.

## Production deploy sequence

1. Confirm the exact revision's CI and local integration gates are green.
2. Confirm SSH, DNS, firewall, free disk and GHCR pull access.
3. Run the installed, root-owned promotion coordinator with the reviewed merge
   SHA and exact published digests. It acquires the maintenance lock before
   reading the mutable checkout or environment, writes a private durable journal,
   fetches and verifies `origin/main`, extracts and syntax-checks the target
   promotion helpers into `/var/lib/bumpabestie`, and runs that target worker as a
   child while retaining the lock:

   ```bash
   target_revision=<full-reviewed-merge-sha>
   /usr/local/sbin/bumpabestie-promote \
     "$target_revision" "sha-<infra-commit>" \
     'ghcr.io/<owner>/bumpabestie-api@sha256:<digest>' \
     'ghcr.io/<owner>/bumpabestie-web@sha256:<digest>' \
     'ghcr.io/<owner>/bumpabestie-caddy@sha256:<digest>' \
     'ghcr.io/<owner>/bumpabestie-postgres@sha256:<digest>' \
     'ghcr.io/<owner>/bumpabestie-backup@sha256:<digest>' \
     'ghcr.io/<owner>/bumpabestie-hermes@sha256:<digest>'
   ```

   Run it as `bumpabestie`, not as root. The inherited descriptor keeps the same
   host lock across target selection, checkout, pointer selection and deploy;
   scheduled/manual backup cannot observe or resume a half-promoted release. The
   worker preserves every non-release setting and the file owner, requires exactly
   one of each of the nine release keys, writes mode `0600`, and renames atomically.
   Never source `.env.production` or print it into a generated file. Before the
   forward boundary, failures restore both the recorded prior pointers and recorded
   operations checkout. A post-migration hybrid rollback deliberately retains the
   target operations checkout. A kill, reboot, corrupt terminal state or failed
   restoration leaves a persistent maintenance interlock; backups and later
   promotions then fail closed until an operator reconciles the journal.

   Every target, rollback, and pre-boundary recovery gate first exercises the
   local Caddy origin at `127.0.0.1` for up to 180 seconds using each production
   hostname, normal CA/SAN verification, and no proxy. A second 60-second gate
   then exercises public edge DNS with any origin override explicitly cleared.

   For the one-time upgrade from a release that predates the coordinator, install
   the reviewed launcher directly from the fetched target commit without checking
   out that commit. This is the only root step; the launcher remains root-owned and
   immutable to the deployment account:

   ```bash
   cd /opt/bumpabestie
   sudo -u bumpabestie -H git fetch --tags --prune origin main
   test "$(sudo -u bumpabestie -H git rev-parse origin/main)" = "$target_revision"
   launcher_tmp="$(mktemp)"
   sudo -u bumpabestie -H git show \
     "$target_revision:infra/bin/bumpabestie-promote" >"$launcher_tmp"
   bash -n "$launcher_tmp"
   install -o root -g root -m 0755 "$launcher_tmp" /usr/local/sbin/bumpabestie-promote
   rm -f "$launcher_tmp"
   sudo -u bumpabestie -H /usr/local/sbin/bumpabestie-promote \
     "$target_revision" "sha-<infra-commit>" \
     '<api-digest-ref>' '<web-digest-ref>' '<caddy-digest-ref>' \
     '<postgres-digest-ref>' '<backup-digest-ref>' '<hermes-digest-ref>'
   ```

4. Confirm `.env.production` validates and its mode is `0600`.
5. Confirm webhook routing and provider selectors match the intended activation state.
6. The deploy script verifies image revision labels, checks the PostgreSQL major,
   inventories extensions and affected legacy BRIN indexes, then creates and
   checksum-verifies a local pre-deploy backup while the old server is running.
7. Install any changed reviewed backup service/timer units as root and run
   `systemctl daemon-reload` before the lock-owning promotion command above. The
   deploy preflight rejects stale installed units.
8. The coordinator checks out the immutable `DEPLOY_REF`; the guarded deploy
   verifies that exact checkout and pulls all required images,
   stops writers, backs up the recovery point, reconciles the database role,
   migrates, imports staged Hermes profiles through a networkless one-shot service,
   starts Hermes gateways and then starts API/web/worker/scheduler/Caddy.
9. Inspect readiness and confirm all provider modes match the intended release.
10. Run and verify the first local backup, then start the timer.
11. Preserve `.deployed-release.json`, DNS/TLS results, service state and backup ID.

Expected service boundary:

```bash
docker compose --env-file .env.production \
  -f compose.yaml -f compose.prod.yaml ps
curl -fsS https://api.bumpabestie.com/health/ready | jq
```

Expected readiness shape:

```json
{
  "status": "ready",
  "database": "ok",
  "async_runtime": {
    "enabled": true,
    "redis": "ok",
    "worker": "ok",
    "scheduler": "ok",
    "queued_wakeups": 0
  },
  "providers": {
    "whatsapp": "meta-or-disabled",
    "bumpa": "bumpa-or-disabled",
    "agent": "hermes-or-disabled"
  }
}
```

An unexpected `mock` or any public Hermes port is a failed production verification.
Worker and scheduler must be healthy. A configured provider mode is not by itself a
live-provider canary.

## Backup timer activation

After one manual backup succeeds and `.env.production` is present:

```bash
sudo systemctl start bumpabestie-backup.service
sudo systemctl enable --now bumpabestie-backup.timer
systemctl list-timers bumpabestie-backup.timer
journalctl -u bumpabestie-backup.service --since today --no-pager
```

The offsite hook exits successfully when `OFFSITE_BACKUP_SCRIPT` is absent. The
systemd unit passes `.env.production` to a narrow parser that reads only this one
executable path; it never sources or exports the application's secrets. A green unit
still proves only the local stage unless a reviewed operator-owned handoff is
configured and the journal contains a separately verified off-host object
ID/checksum. See `docs/runbook.md`.

Recovery-point backup `20260713T121212Z` was created immediately before the current
promotion. Its manifest records application revision
`f400cfe67628da787f7ee2a3a3f42c78cb6fae3f`, schema
`0008_bumpa_dataset_failures`, PostgreSQL 16.14 and backup image
`ghcr.io/makriman/bumpabestie-backup@sha256:7608a1557d31d4d6f985807f05ed50a6001dc33a7c9c704938fc7fab0221cc58`.
All five checksums and every archive/dump parser check passed before release
`8f290509668de15eaf3621e3213f4276f85a0a83` was started. The timer is enabled.
This proves the local recovery point only; off-host copy and remote restore evidence
remain open.

## Five-store production onboarding

The 2026-07-13 production database audit verifies all five
owner/tenant/phone/Bumpa mappings and the intended dual global-admin/demo
membership. The workflow below remains the fail-closed method for re-auditing or
reconciling those mappings; do not create duplicates merely to rerun a canary.

Use `scripts/production_onboard.py` from a trusted operator workstation. The
credential file must be a regular `0600` file and credentials are streamed to the
API container over SSH stdin; they are never placed in argv, environment variables,
logs, or temporary files. The explicitly approved dual-role operator/owner mapping
must be acknowledged on every command:

```bash
./scripts/production_onboard.py check --allow-operator-owner-overlap
./scripts/production_onboard.py plan --allow-operator-owner-overlap
```

Take and checksum-verify a fresh production backup after the plan succeeds. Then
apply all five tenants:

```bash
./scripts/production_onboard.py onboard --allow-operator-owner-overlap
```

`onboard` plans all stores, applies each store transactionally, and immediately
runs a redacted invariant audit covering all five tenant/owner/phone/Bumpa mappings,
the encrypted credentials, onboarding audit rows, and the single superadmin/owner
overlap. Any mismatch is a hard stop before Hermes or Bumpa canaries. The audit can
also be rerun independently:

```bash
./scripts/production_onboard.py audit --allow-operator-owner-overlap
./scripts/production_onboard.py hermes --live-chat --allow-operator-owner-overlap
./scripts/production_onboard.py sync --allow-operator-owner-overlap
```

Authenticated canary HTTP calls reject every redirect. Sync evidence is correlated
through the exact durable job ID and sync-run ID returned by the API, rather than by
time or date heuristics. Existing opt-outs, inactive users, suspended tenants,
revoked memberships, unapproved phone identities, and inactive Bumpa connections
fail closed; reactivation is a separate administrative decision.

## Rollback boundary

`scripts/deploy.sh` records the verified revision and actual running repository
digests. It validates that record as private, immutable metadata and compares its
API, worker, scheduler, web, Hermes, Caddy, PostgreSQL and Redis references with
the actual running containers before enabling automatic rollback. A mismatch is a
hard preflight stop. Any pre-deployment failure after a valid record is loaded
atomically restores all nine prior release pointers in `.env.production`.

After migrations begin, a failed later release can restore the prior application
images and rerun smoke. Caddy, PostgreSQL and Redis remain forward-only:
automatic rollback never recreates an older infrastructure image and never reverses
migrations. Only after rollback smoke passes, the script persists a hybrid boundary:
the prior application revision/tag and API/web/Hermes references with the target
infrastructure tag and Caddy/PostgreSQL/backup references. Its separate
`operations_revision` remains the target checkout so subsequent recovery and
promotion use schema-compatible tooling; older records default this field to their
application `revision`. If either smoke or
persistence fails, it leaves the deployment failed for operator intervention and
does not claim a verified rollback. Before the recovery-point backup, the deploy script quiesces every
application writer; if backup or compatibility validation then fails, it restarts
the exact previously running containers. Forward/backward schema compatibility
remains a release requirement. Never run a destructive down-migration during an
outage without a separately reviewed recovery plan.

Migration `0007_legacy_sync_writer` preserves the application-image rollback
boundary introduced by `0006_sync_completion`. SQL that omits the new completion
evidence receives the server-only quality `legacy`; the current ORM still writes
`pending` explicitly and must transition to a fully typed terminal state. Legacy
rows may finish under a pre-0006 writer, but remain excluded from trusted Bumpa
freshness and chat context until a current writer records a new evidenced run.
The 0007 downgrade refuses while any legacy row remains; it never fabricates
completion evidence merely to satisfy the older constraint.

Migration `0008_bumpa_dataset_failures` keeps that hybrid rollback boundary
forward-compatible while adding typed evidence for provider calls that received
no HTTP response. `http_status` is nullable only when `failure_kind` explicitly
records `timeout` or `transport`; HTTP gateway failures retain their real status.
A pre-0008 writer may continue omitting the nullable `failure_kind` for HTTP
responses. The downgrade refuses while status-less evidence exists instead of
inventing an HTTP status. A hybrid rollback therefore retains the target
operations checkout and schema 0008 while the prior application image continues
writing its older HTTP-only raw-response shape.

## Optional MCP OAuth clients

Google and Meta Ads connections remain disabled until their provider OAuth clients
are approved. When activating one, keep its client secret out of `.env.production`:
write it to a private absolute host file with mode `0400` or `0600`, then set the
public client ID and corresponding host path. The one-shot secret initializer copies
it into the API/worker/scheduler runtime volume as a `0400` file.

```bash
MCP_GOOGLE_OAUTH_ENABLED=true
GOOGLE_OAUTH_CLIENT_ID=approved-google-client-id
GOOGLE_OAUTH_CLIENT_SECRET_FILE_HOST=/var/lib/bumpabestie/google_oauth_client_secret

MCP_META_ADS_OAUTH_ENABLED=true
META_ADS_OAUTH_CLIENT_ID=123456789012345
META_ADS_OAUTH_CLIENT_SECRET_FILE_HOST=/var/lib/bumpabestie/meta_ads_oauth_client_secret
```

Leave the inline `*_CLIENT_SECRET` values blank. Production validation rejects an
enabled connector whose host file is missing, symlinked, broadly readable, or still
mapped to `/dev/null`. Register the following exact redirect URI with each enabled
OAuth provider, replacing the hostname with `PUBLIC_ORIGIN`:

```text
https://bumpabestie.example.com/api/backend/settings/mcp-oauth/callback
```

The callback intentionally returns through the same-origin web proxy. Keep
`SESSION_COOKIE_DOMAIN` blank: the host-only HttpOnly session cookie is sufficient
and does not need to be exposed to the API, admin, or research subdomains.

## Proactive SME insights and external alerts

Both capabilities are disabled by default. Keep proactive insights disabled until
Meta approves `bb_daily_insight` and `bb_weekly_insight`; each template has one body
text variable for the bounded aggregate summary. The scheduler evaluates active,
research-consented tenants in their IANA timezone. At execution, the worker again
checks consent, active owner membership, approved identity, STOP opt-out and typed
Bumpa freshness. Calendar-slot and message fences prevent duplicate sends.

For external alerts, create a 32+ character HMAC secret in an absolute private host
file readable by the sandboxed `bumpabestie` systemd services (recommended
`/var/lib/bumpabestie/ops_alert_webhook_hmac_secret`, owner
`bumpabestie:bumpabestie`, mode `0400`) and configure:

```bash
OPS_ALERTS_ENABLED=true
OPS_ALERT_WEBHOOK_URL=https://alerts.example.com/v1/bumpabestie-events
OPS_ALERT_HMAC_SECRET_FILE_HOST=/var/lib/bumpabestie/ops_alert_webhook_hmac_secret
OPS_ALERT_HMAC_SECRET_FILE=/run/runtime-secrets/ops_alert_hmac_secret
```

Create `/etc/bumpabestie/alerts.json` owned by `root:bumpabestie` with mode `0640`
for host disk/backup alerts. It is a narrow fixed-config file, not the application
environment:

```json
{
  "webhook_url": "https://alerts.example.com/v1/bumpabestie-events",
  "hmac_secret_file": "/var/lib/bumpabestie/ops_alert_webhook_hmac_secret",
  "max_attempts": 3,
  "timeout_seconds": 10
}
```

The optional secret is copied into the app runtime-secret volume only when the host
file exists. Payloads use fixed summaries, allowlisted categories, aggregate host
percentages and opaque HMAC/hash references—never phone numbers, raw tenant/source
IDs, provider bodies or credentials. Receivers verify `X-BumpaBestie-Signature`
over `<X-BumpaBestie-Timestamp>.<raw-body>` and deduplicate `Idempotency-Key`.

## GitHub controls before launch

Enable secret scanning and push protection, Dependabot security updates, private
vulnerability reporting and protected `main`. Require all CI jobs, one review,
conversation resolution, linear history and protection against force-push/deletion.
Restrict image publication and release-tag creation to trusted maintainers.

There is no remote deploy workflow today. Manual SSH deployment is the intended
baseline. A future workflow must use a protected GitHub Environment, manual
approval and a least-privilege deploy credential; product API keys must stay out of
GitHub unless a formal secret-management policy is adopted.
