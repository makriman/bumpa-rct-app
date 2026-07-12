# Deployment

## Current release boundary

The deployable target is a six-image production stack: API, web, Caddy, PostgreSQL,
backup and the pinned Hermes runtime. Redis remains an upstream digest-pinned
service. Worker and scheduler run from the API image and use Postgres jobs/outbox as
the source of truth with Redis wake-ups and heartbeats. Production always requires
`ASYNC_RUNTIME_ENABLED=true`; provider selectors may be disabled for containment or
set to `meta`, `bumpa` and `hermes` only after their activation gates pass.

As of 2026-07-12, hardened release
`54bb8e9b29295171d65972e094e508d25a7bc53d` is live as this disabled baseline
on five temporary sslip.io hosts with valid TLS. [PR 14](https://github.com/makriman/bumpa-rct-app/pull/14),
[PR CI 29194474957](https://github.com/makriman/bumpa-rct-app/actions/runs/29194474957),
[main CI 29194621699](https://github.com/makriman/bumpa-rct-app/actions/runs/29194621699)
and [publish run 29194814472](https://github.com/makriman/bumpa-rct-app/actions/runs/29194814472)
are its release gates. Exact deployed image index references are recorded in
`docs/verification.md`.

Production verification found exactly five healthy services with zero restarts,
Caddy 2.11.4 built with Go 1.26.5 running as UID 10001 with restricted
capabilities, PostgreSQL 16.14, Redis 7.4.9, disabled-provider readiness, negative
API-docs/OTP canaries and correct desktop/mobile presentation. Backup
`20260712T140353Z` passed its historical format-2 manifest and checksum checks; the nightly
timer is enabled. This does not activate providers or authorize real traffic.

Do not change a provider selector from `disabled` merely because a credential has
been obtained. Use the activation gates in `docs/build-plan-compliance.md`.

## External prerequisites

The following are outside the repository and are still required:

1. DigitalOcean console access and an authorized SSH public key for a non-root
   deploy user. Never share or commit the private key.
2. DNS-provider access to create A records for the apex, `www`, `api`, `admin` and
   `research` hosts.
3. Published immutable API/web/Caddy/PostgreSQL/backup/Hermes images and either public
   GHCR packages or a least-privilege host credential with `read:packages` only.
4. An encrypted off-host backup target and its host-only credential.
5. An alert destination and an identified operator for deployment and restore.

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
3. Confirm `.env.production` validates and its mode is `0600`.
4. Confirm webhook routing and provider selectors match the intended activation state.
5. The deploy script verifies image revision labels, checks the PostgreSQL major,
   inventories extensions and affected legacy BRIN indexes, then creates and
   checksum-verifies a local pre-deploy backup while the old server is running.
6. Install any changed reviewed backup service/timer units as root, run
   `systemctl daemon-reload`, then run `./scripts/deploy.sh` as `bumpabestie` from a
   clean checkout. The deploy preflight rejects stale installed units.
7. The script checks out the immutable `DEPLOY_REF`, pulls all required images,
   stops writers, backs up the recovery point, reconciles the database role,
   migrates, imports staged Hermes profiles through a networkless one-shot service,
   starts Hermes gateways and then starts API/web/worker/scheduler/Caddy.
8. Inspect readiness and confirm all provider modes match the intended release.
9. Run and verify the first local backup, then start the timer.
10. Preserve `.deployed-release.json`, DNS/TLS results, service state and backup ID.

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

For the hardened baseline, backup `20260712T140353Z` was verified against release
`54bb8e9b29295171d65972e094e508d25a7bc53d` and the exact backup image reference.
The timer is enabled; its next recorded run is `2026-07-13 02:32 UTC`. This proves
only the local stage. Off-host copy and remote restore evidence remain open.

## Rollback boundary

`scripts/deploy.sh` records the verified revision and actual running repository
digests. On a failed later release it can restore the prior API and web image
references and rerun smoke. Caddy, PostgreSQL and Redis remain forward-only:
automatic rollback never recreates an older infrastructure image and never reverses
migrations. Before the recovery-point backup, the deploy script quiesces every
application writer; if backup or compatibility validation then fails, it restarts
the exact previously running containers. Forward/backward schema compatibility
remains a release requirement. Never run a destructive down-migration during an
outage without a separately reviewed recovery plan.

## GitHub controls before launch

Enable secret scanning and push protection, Dependabot security updates, private
vulnerability reporting and protected `main`. Require all CI jobs, one review,
conversation resolution, linear history and protection against force-push/deletion.
Restrict image publication and release-tag creation to trusted maintainers.

There is no remote deploy workflow today. Manual SSH deployment is the intended
baseline. A future workflow must use a protected GitHub Environment, manual
approval and a least-privilege deploy credential; product API keys must stay out of
GitHub unless a formal secret-management policy is adopted.
