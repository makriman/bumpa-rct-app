# Deployment

## Current release boundary

The deployable target at this stage is a **provider-disabled infrastructure
baseline**, not a production SME pilot. It is useful for proving host hardening,
DNS/TLS, immutable image delivery, migrations, network boundaries, readiness and
backup mechanics. It must not receive SME or research traffic.

In this baseline:

- Caddy, web, API, Postgres and Redis may run.
- `WHATSAPP_BACKEND=disabled`, `BUMPA_BACKEND=disabled` and
  `AGENT_BACKEND=disabled` are required.
- `ASYNC_RUNTIME_ENABLED=false` is required.
- worker and scheduler are not started because no production queue adapter exists.
- no Hermes service or profile runtime exists.
- OTP delivery, provider sync, agent chat and production report generation must
  return a clear unavailable response instead of using local mocks.
- `/health/ready` proves database access and reports configured provider modes; it
  does not probe Meta, Bumpa or Hermes.

Do not change a provider selector from `disabled` merely because a credential has
been obtained. Use the activation gates in `docs/build-plan-compliance.md`.

## External prerequisites

The following are outside the repository and are still required:

1. DigitalOcean console access and an authorized SSH public key for a non-root
   deploy user. Never share or commit the private key.
2. DNS-provider access to create A records for the apex, `www`, `api`, `admin` and
   `research` hosts.
3. Published immutable API/web images and either public GHCR packages or a
   least-privilege host credential with `read:packages` only.
4. An encrypted off-host backup target and its host-only credential.
5. An alert destination and an identified operator for deployment and restore.

Meta, Bumpa and Anthropic/Hermes credentials are **not** prerequisites for the
disabled baseline. They belong to later provider activation work. Claude is called
through Hermes for the SME-agent path; its Anthropic key must be injected only into
the future Hermes secret boundary, never the shared application environment.

## Host prerequisites

- Ubuntu 24.04 LTS on `linux/amd64`, a stable IP, provider backups and monitoring.
- Cloud firewall: public TCP 80/443; SSH 22 only from a trusted `/32` or VPN CIDR.
- SSH keys only, non-root `bumpabestie` deploy user and `/opt/bumpabestie` mode 0750.
- No public Postgres, Redis, Docker API or future Hermes ports.
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

For the provider-disabled baseline, the important mode controls are:

```bash
APP_ENV=production
SESSION_COOKIE_SECURE=true
EXPOSE_LOCAL_OTP=false
SEED_DEMO_DATA=false
CADDY_SITE_SCHEME=https
CADDY_BIND_ADDRESS=0.0.0.0
WHATSAPP_BACKEND=disabled
BUMPA_BACKEND=disabled
AGENT_BACKEND=disabled
ASYNC_RUNTIME_ENABLED=false
```

Do not include `DEV_FIXED_OTP` or `DEV_OTP_SINK`. Set the five production domains
and HTTPS origins, independent high-entropy application/database secrets, internal
database URLs, `GHCR_OWNER`, immutable `DEPLOY_REF` and
`IMAGE_TAG=sha-<full-commit-sha>`. `IMAGE_TAG=latest` is forbidden.

`ANTHROPIC_API_KEY` and model settings do not belong in the shared API/web/worker/
scheduler environment. Per-tenant Bumpa keys do not belong in this file; the future
admin workflow stores them encrypted in Postgres.

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

Pull-request CI builds `linux/amd64` API and web images without publishing them.
`.github/workflows/publish-images.yml` publishes both images to GHCR after it finds
a successful CI run for the exact commit. The deployable tag is
`sha-<full-commit-sha>`; a release tag is an alias, and `latest` is never emitted.
The build requests provenance and SBOM attestations.

Before deploy, record both image digests and scan the exact published images. A
successful build or SBOM is not a vulnerability scan. If GHCR packages remain
private, log in on the host using a dedicated pull-only credential and ensure the
credential file is owned by the deploy user with restrictive permissions.

## Provider-disabled deploy sequence

1. Confirm the exact revision's CI and local integration gates are green.
2. Confirm SSH, DNS, firewall, free disk and GHCR pull access.
3. Confirm `.env.production` validates and its mode is `0600`.
4. Confirm no real user traffic or provider webhooks point to this baseline.
5. The deploy script creates a local pre-deploy backup when Postgres is already
   running; verify that step rather than bypassing it.
6. Run `./scripts/deploy.sh` as `bumpabestie` from a clean checkout.
7. The script checks out the immutable `DEPLOY_REF`, pulls all required images,
   migrates, removes worker/scheduler, starts the baseline services, verifies
   health/restart counts and runs HTTPS smoke checks.
8. Inspect readiness and confirm all provider modes are `disabled`.
9. Run and verify the first local backup, then start the timer.
10. Record revision, image digests, DNS/TLS results, service state and backup ID.

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
  "providers": {
    "whatsapp": "disabled",
    "bumpa": "disabled",
    "agent": "disabled"
  }
}
```

An unexpected `mock`, running worker/scheduler, or any Hermes port is a failed
baseline verification. A readiness 200 with disabled providers is expected; it is
not a provider health claim.

## Backup timer activation

After one manual backup succeeds and `.env.production` is present:

```bash
sudo systemctl start bumpabestie-backup.service
sudo systemctl enable --now bumpabestie-backup.timer
systemctl list-timers bumpabestie-backup.timer
journalctl -u bumpabestie-backup.service --since today --no-pager
```

The current offsite hook exits successfully when `OFFSITE_BACKUP_SCRIPT` is absent.
The current systemd unit does not load that setting from `.env.production`; setting
it there alone is not activation. Therefore a green systemd unit proves only the
local backup unless a reviewed unit/credential boundary invokes the handoff and the
journal contains a separately verified off-host object ID/checksum. See
`docs/runbook.md`.

## Rollback boundary

`scripts/deploy.sh` records the verified revision and, on a failed later release,
automatically attempts to restore the previous API/web images and rerun smoke. It
does not reverse database migrations, so forward/backward migration compatibility
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
