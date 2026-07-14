# Deployment

## Current release boundary

The deployable target is a six-image production stack: API, web, Caddy, PostgreSQL,
backup and the pinned Hermes runtime. Redis remains an upstream digest-pinned
service. Worker and scheduler run from the API image and use Postgres jobs/outbox as
the source of truth with Redis wake-ups and heartbeats. Production always requires
`ASYNC_RUNTIME_ENABLED=true`; provider selectors may be disabled for containment or
set to `meta`, `bumpa` and `hermes` after scoped configuration readiness is
evidenced. A selected adapter is not unrestricted-traffic approval: every
unsupported or externally blocked capability remains fail-closed until its full
activation gate passes.

As of 2026-07-14, release
`0ec2c58f8b0a26734ca08788787640dca1409821` is live on the five branded
`bumpabestie.com` hosts through Cloudflare. Temporary web-login
[PR 45](https://github.com/makriman/bumpa-rct-app/pull/45), exact-revision
[main CI 29333098858](https://github.com/makriman/bumpa-rct-app/actions/runs/29333098858)
(13/13 jobs) and
[publish run 29333505495](https://github.com/makriman/bumpa-rct-app/actions/runs/29333505495)
(7/7 jobs) complete the current build and publication gates. The deployed release
record matches all six promoted release indexes, and the eight running services
use their intended exact references. The redacted production transcript is
[`docs/release-evidence-0ec2c58.md`](release-evidence-0ec2c58.md).

Production verification found seven long-running application services using their
intended successor references and Redis using its separately pinned upstream
digest. All eight services were running, all seven configured healthchecks were
healthy, and every container had zero restarts, OOM kills or unhealthy states at
schema `0013_web_pin_challenges`. Temporary web sign-in is
limited to exactly five existing mapped collaborators and preserves the existing
role boundary. Acceptance covered every collaborator on public chat,
administration and research: five browser and ten API/BFF sign-ins completed the
15 host-scoped combinations. Wrong-code and unmapped canaries failed generically,
sibling hosts did not inherit cookies, role gates held, logout revoked sessions,
and the final active-session/challenge counts were zero.

Historical predecessor evidence recorded five correlated Bumpa runs with orders
available. Stores 1–4 returned accepted-partial 8/10 analytics datasets; degraded
store 5 returned 7/10 because `products.overview` timed out/returned HTTP 504. The
same predecessor evidence recorded five Hermes health/completion canaries, 40
rejected foreign-profile gateway/control attempts, and an audited restart plus
post-restart completion. Those provider canaries were not rerun for this web
release. The current release does not activate WhatsApp: WhatsApp authentication,
Meta test-sender verification and proactive/daily/weekly delivery are disabled. The
operational WhatsApp/outbox fingerprint was unchanged by the complete web-login
acceptance matrix; no Meta send or delivery receipt is claimed.

The prior release's local recovery points remain historical evidence only. A new
exact-successor local backup passed its guarded wrapper, five-artifact checksum
replay and format-3 revision/schema/image manifest checks. The stability observation
also passed: every container had at least 20 minutes of continuous uptime, five
closing samples retained all eight IDs, and exact images, seven healthchecks,
readiness, public smoke, severe/exit-signal logs, firewall, interlock and timers
remained clean. Backup and disk-usage timers are active. All five DNS records are
Cloudflare-proxied; Full (strict), Always Use HTTPS, TLS 1.2 minimum and TLS 1.3 are
enabled. TLS 1.0/1.1 are rejected on every host, `www` canonically redirects to the
apex with path/query intact, and dynamic documents have unique nonce-based CSP.
Provider selectors and valid credentials do not by themselves authorize real
tenant traffic. Complete Bumpa provider coverage, a launch-ready Meta sender with
approved authentication templates and outbound-delivery evidence, off-host
durability, a real alert destination and privacy/security/retention approval remain open
gates.

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
- Cloud firewall: SSH 22 only from a trusted `/32` or VPN CIDR. While branded
  DNS is Cloudflare-proxied, origin TCP 80/443 must accept only Cloudflare's
  current published IPv4/IPv6 ranges; an `Anywhere` origin rule is not a live
  production configuration.
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

Bootstrap's public 80/443 rules are a temporary certificate/DNS bring-up state,
not the Cloudflare-proxied live state. Complete the origin-firewall procedure
below before declaring the branded hosts production-ready.

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

## Cloudflare origin firewall

The orange-cloud DNS proxy does not, on its own, stop a client from resolving the
origin address elsewhere and sending the branded `Host` header directly. Three
host checks form one boundary: UFW protects normal host ingress, a managed raw
`PREROUTING` chain drops non-Cloudflare web traffic before DNAT, and a managed
`DOCKER-USER` chain repeats that decision after Docker starts. UFW alone is not
acceptable because Docker-published ports bypass its `INPUT` chain. A post-Docker
oneshot alone is also unacceptable because a restarted Docker daemon can restore
published ports before that oneshot runs.

After all five records are proxied and the edge-to-origin health check passes,
keep an already-tested SSH session open and use the same canonical administrator
CIDR recorded during bootstrap. Start with the read-only UFW plan:

```bash
sudo python3 ./scripts/configure_cloudflare_ufw.py plan \
  --ssh-cidr 203.0.113.10/32
```

The command retrieves `https://www.cloudflare.com/ips-v4/` and
`https://www.cloudflare.com/ips-v6/` directly over verified TLS. It fails closed
on an empty, malformed, non-canonical, non-global, mixed-family, duplicate or
overlapping response; an inactive/non-deny UFW baseline; disabled UFW IPv6
support; broad or secondary SSH `ALLOW IN`/`LIMIT IN` access; all-port,
application-profile, unknown or unmanaged inbound allows or limits; and any rule
needing human review. It preflights every
proposed UFW allow and reports only rule counts.

Review the plan and apply with the explicit confirmation phrase:

```bash
sudo python3 ./scripts/configure_cloudflare_ufw.py apply \
  --ssh-cidr 203.0.113.10/32 \
  --confirm restrict-origin-to-cloudflare
```

The apply is additive before it is subtractive. It snapshots `/etc/ufw`, ensures
and verifies the nominated SSH allow, adds and verifies every current Cloudflare
range for TCP 80/443, and only then removes broad `Anywhere` rules and stale rules
managed by this script. A process lock prevents concurrent runs. Any failure after
the snapshot triggers restoration and reload of the complete prior UFW state.
Re-running the command against a compliant host makes no firewall changes. This
step does not yet close Docker's forwarding path.

Install the reviewed Docker boundary executable without enabling its unit:

```bash
sudo install -m 0755 -o root -g root \
  ./scripts/cloudflare_docker_firewall.py \
  /usr/local/sbin/bumpabestie-cloudflare-docker-firewall
ip -br address show scope global
ip -4 route show default
ip -6 route show default
```

The current single-interface Droplet uses the same external interface for both
families. Substitute the externally verified interface name below; the tool
rejects a bridge or other interface that does not match the host's unambiguous
default ingress. Docker must use its iptables backend and both IPv4 and IPv6
`DOCKER-USER` chains must exist. The verified host uses the `iptables`/`ip6tables`
v1.8.10 nf_tables compatibility frontends with Docker's iptables firewall
backend; that combination is supported. Docker's separate native nftables
firewall backend is not supported by this tool.

```bash
sudo /usr/local/sbin/bumpabestie-cloudflare-docker-firewall plan \
  --ipv4-interface eth0 --ipv6-interface eth0
sudo /usr/local/sbin/bumpabestie-cloudflare-docker-firewall refresh \
  --ipv4-interface eth0 --ipv6-interface eth0 \
  --confirm enforce-cloudflare-in-docker-user
sudo /usr/local/sbin/bumpabestie-cloudflare-docker-firewall verify-state
```

`refresh` retrieves and strictly validates the same official lists, applies and
verifies both live families, then atomically commits a root-owned `0600` state
file. It installs `BUMPABESTIE_CF_PRE` first in raw `PREROUTING`, before DNAT,
and `BUMPABESTIE_CF_P` first in `DOCKER-USER`, after Docker. For ports 80 and
443, current Cloudflare sources return to normal processing and all other sources
drop; each chain's final `RETURN` preserves non-web traffic. There is deliberately
no blanket established-connection bypass. Reapplication first verifies the exact
ordered rules and is mutation-free when already compliant. A failed first refresh
retains an emergency empty-allowlist web deny in both layers; a failed update
restores the previously verified state.

Only after `verify-state` passes, install and enable persistence:

```bash
sudo install -m 0644 -o root -g root \
  ./infra/systemd/bumpabestie-cloudflare-origin-pregate.service \
  /etc/systemd/system/bumpabestie-cloudflare-origin-pregate.service
sudo install -d -m 0755 -o root -g root \
  /etc/systemd/system/docker.service.d
sudo install -m 0644 -o root -g root \
  ./infra/systemd/docker.service.d/10-bumpabestie-cloudflare-origin-pregate.conf \
  /etc/systemd/system/docker.service.d/10-bumpabestie-cloudflare-origin-pregate.conf
sudo install -m 0644 -o root -g root \
  ./infra/systemd/bumpabestie-cloudflare-docker-firewall.service \
  /etc/systemd/system/bumpabestie-cloudflare-docker-firewall.service
sudo install -m 0644 -o root -g root \
  ./infra/systemd/bumpabestie-cloudflare-docker-firewall-failure.service \
  /etc/systemd/system/bumpabestie-cloudflare-docker-firewall-failure.service
sudo systemctl daemon-reload
sudo systemctl start bumpabestie-cloudflare-origin-pregate.service
sudo systemctl enable --now bumpabestie-cloudflare-docker-firewall.service
sudo systemctl is-enabled bumpabestie-cloudflare-docker-firewall.service
sudo systemctl is-active bumpabestie-cloudflare-docker-firewall.service
```

The Docker drop-in requires the pre-gate oneshot and orders Docker after it. The
pre-gate has no `RemainAfterExit`, so every Docker activation re-reads persistent
state and verifies raw `PREROUTING` before Docker can publish anything; a failed
pre-gate prevents Docker from starting. The second oneshot is ordered after and
bound to Docker and reproduces `DOCKER-USER` as defense in depth. It has no stop
action that removes live rules. If its normal state application and emergency
deny both fail, its failure handler stops Docker. A pre-gate failure also invokes
the same stop handler, covering a manual reapply while Docker is already active.
Bootstrap installs only the executable when no state exists; it installs the
units and Docker drop-in only after persistent state and both live layers already
pass `verify-state`.

In a maintenance window, prove restart persistence before removing any temporary
operator-created chain:

```bash
sudo systemctl restart docker.service
sudo systemctl is-active bumpabestie-cloudflare-docker-firewall.service
sudo systemctl show bumpabestie-cloudflare-origin-pregate.service \
  --property=Result --property=ExecMainStatus
sudo /usr/local/sbin/bumpabestie-cloudflare-docker-firewall verify-state
sudo iptables -t raw -S PREROUTING
sudo ip6tables -t raw -S PREROUTING
sudo iptables -S DOCKER-USER
sudo ip6tables -S DOCKER-USER
```

If a temporary `BUMPABESTIE_CF` chain protected the live gap, retain it through
all steps above. Remove only that temporary hook and chain after the distinct
`BUMPABESTIE_CF_P` state survives the Docker restart and both positive/negative
external probes pass. Never flush `DOCKER-USER` or restore the complete filter
table; both actions can destroy Docker and unrelated forwarding rules.

Record the printed root-only rollback snapshot path. From a second external
terminal, verify all of the following without closing the first SSH session:

```bash
sudo ufw status numbered
SMOKE_SCHEME=https SMOKE_PORT=443 \
  APP_DOMAIN=bumpabestie.com WWW_DOMAIN=www.bumpabestie.com \
  API_DOMAIN=api.bumpabestie.com ADMIN_DOMAIN=admin.bumpabestie.com \
  RESEARCH_DOMAIN=research.bumpabestie.com \
  ./scripts/smoke_test.sh
```

- SSH still works from the nominated CIDR.
- UFW has no broad SSH allow/limit, TCP 80/443 `Anywhere`, application-profile,
  all-port or other unmanaged inbound allow/limit.
- Both raw `PREROUTING` families and both `DOCKER-USER` families have the
  persistent hook first and pass `verify-state`; unrelated ports retain their
  previous behavior.
- Every normal hostname still resolves to Cloudflare and the production smoke
  gate passes through the edge.
- From a non-Cloudflare external network, a direct-origin probe such as
  `curl --noproxy '*' --resolve bumpabestie.com:443:<origin-ip>
https://bumpabestie.com/` cannot connect. Do not run that negative probe from a
  Cloudflare address.

If the Docker boundary update fails, use its printed snapshot first. An update
snapshot restores and verifies the prior allowlist. When no prior persistent
state existed, rollback intentionally retains emergency web deny instead of
reopening the origin:

```bash
sudo /usr/local/sbin/bumpabestie-cloudflare-docker-firewall rollback \
  --backup /var/lib/bumpabestie/firewall-backups/docker/<printed-snapshot> \
  --confirm restore-previous-docker-firewall
```

If the earlier UFW update itself must be reverted, keep the original SSH session
open and restore its separately printed snapshot:

```bash
sudo python3 ./scripts/configure_cloudflare_ufw.py rollback \
  --backup /var/lib/bumpabestie/firewall-backups/<printed-snapshot> \
  --confirm restore-previous-ufw-rules
sudo ufw status verbose
```

The rollback rejects paths outside the configured root and verifies every
snapshotted file against the root-only manifest before staging and swapping the
configuration directory and reloading UFW. After rollback, diagnose the failed
edge or SSH condition before attempting a new plan. Do not call either boundary
complete until edge-positive requests pass, direct-origin TCP 80 and 443 time out
from a non-Cloudflare network, SSH remains available, and a Docker restart has
run the successful pre-gate before Docker and reproduced and verified both
ordered IPv4/IPv6 layers.

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
AUTH_LOGIN_MODE=whatsapp_otp
WHATSAPP_BACKEND=meta
BUMPA_BACKEND=bumpa
AGENT_BACKEND=hermes
ASYNC_RUNTIME_ENABLED=true
SECRETS_DIR=/var/lib/bumpabestie-secrets
```

`AUTH_LOGIN_MODE` is independent from the provider selectors: `disabled` is the
authentication kill switch, `whatsapp_otp` uses the activated WhatsApp OTP lane,
and `temporary_static_pin` enables the short-lived mapped-collaborator web pilot.
For the temporary web-only containment release, set:

```dotenv
AUTH_LOGIN_MODE=temporary_static_pin
TEMPORARY_WEB_PIN_VERIFIER_FILE=/run/auth-secret/temporary_web_pin_verifier
TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST=
TEMPORARY_WEB_PIN_EXPIRES_AT=<future-timezone-aware-timestamp>
WHATSAPP_BACKEND=disabled
META_TEST_SENDER_VERIFICATION_MODE=disabled
PROACTIVE_INSIGHTS_ENABLED=false
DAILY_INSIGHTS_ENABLED=false
WEEKLY_INSIGHTS_ENABLED=false
```

Leave `TEMPORARY_WEB_PIN_VERIFIER` blank in the host environment. The non-secret
`TEMPORARY_WEB_PIN_VERIFIER_FILE` must equal the fixed API-only runtime path shown
above while this mode is active.
Before first activation, leave `TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST` blank. The
root-only setter creates an exclusive `0600` version under the root-owned `0700`
`/var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/` directory and
atomically writes that generated path into the environment. Never choose or reuse
a version filename manually, and do not place these files under the shared
deploy-user-owned `SECRETS_DIR`. Provision the HMAC verifier through
`scripts/set_temporary_login_pin.sh`, validate the now-complete environment, and use the
guarded promotion path. The non-root deploy preflight invokes a fixed root-owned
helper for host metadata checks before an exact-file check through the already-pulled
API image in a networkless, read-only, capability-free container; neither step emits
the verifier. The mutable checkout validator is never elevated. The Docker-enabled
deploy account is a trusted root-equivalent production principal, not an isolation
boundary. Meta secret files remain in
their existing scoped boundary for later activation, but no login, test-sender or
proactive delivery may use them while this mode is selected. The full threat model,
rotation/rollback procedure, role boundary, client-IP chain and acceptance gates
are in [`docs/temporary-web-login.md`](temporary-web-login.md).

The first production rollout used the required two-phase sequence: it promoted
and verified the exact successor with authentication disabled and WhatsApp parked,
then staged temporary mode and promoted the same revision and six digests again.
Future first-time deployments must preserve this ordering. Planned verifier
rotations on the already compatible release use the guarded setter and coordinator
without restaging the introduction phase.

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
3. From a root login shell, fetch the reviewed target, install its root-owned
   promotion prerequisites, and only then start the coordinator as `bumpabestie`
   with the reviewed merge SHA and exact published digests. Do not attempt the
   promotion before the helper and policy block below succeeds. The coordinator
   acquires the maintenance lock before
   reading the mutable checkout or environment, writes a private durable journal,
   fetches and verifies `origin/main`, extracts and syntax-checks the target
   promotion helpers into `/var/lib/bumpabestie`, and runs that target worker as a
   child while retaining the lock.

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

   Run the following block from that root login shell before every promotion. It
   installs the reviewed temporary-auth validator and its narrow sudoers rule
   directly from the fetched target commit without
   checking out that commit. The deploy preflight byte-compares the installed
   helper with the target checkout and fails before the forward boundary when it is
   missing or stale. Installing a coordinator does not imply that this newer helper
   is present. The block also re-installs the reviewed launcher; this makes a
   coordinator upgrade explicit and is idempotent when its bytes are unchanged.
   These root-owned executable/policy changes remain immutable to the deployment
   account:

   ```bash
   target_revision=<full-reviewed-merge-sha>
   cd /opt/bumpabestie
   sudo -u bumpabestie -H git fetch --tags --prune origin main
   test "$(sudo -u bumpabestie -H git rev-parse origin/main)" = "$target_revision"
   launcher_tmp="$(mktemp)"
   validator_tmp="$(mktemp)"
   sudoers_tmp="$(mktemp)"
   sudo -u bumpabestie -H git show \
     "$target_revision:infra/bin/bumpabestie-promote" >"$launcher_tmp"
   sudo -u bumpabestie -H git show \
     "$target_revision:scripts/validate_temporary_auth_secret.sh" >"$validator_tmp"
   sudo -u bumpabestie -H git show \
     "$target_revision:infra/sudoers/bumpabestie-temporary-auth-secret" >"$sudoers_tmp"
   bash -n "$launcher_tmp"
   bash -n "$validator_tmp"
   visudo -cf "$sudoers_tmp"
   install -o root -g root -m 0755 "$launcher_tmp" /usr/local/sbin/bumpabestie-promote
   install -o root -g root -m 0755 "$validator_tmp" \
     /usr/local/sbin/bumpabestie-validate-temporary-auth-secret
   install -o root -g root -m 0440 "$sudoers_tmp" \
     /etc/sudoers.d/bumpabestie-temporary-auth-secret
   visudo -cf /etc/sudoers.d/bumpabestie-temporary-auth-secret
   cmp -s "$launcher_tmp" /usr/local/sbin/bumpabestie-promote
   cmp -s "$validator_tmp" \
     /usr/local/sbin/bumpabestie-validate-temporary-auth-secret
   rm -f "$launcher_tmp" "$validator_tmp" "$sudoers_tmp"
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

The previously verified recovery points belong to the predecessor release, but a
new exact-successor local backup is recorded in
[`docs/release-evidence-0ec2c58.md`](release-evidence-0ec2c58.md). Its guarded
wrapper, five expected artifacts, SHA-256 replay and format-3 manifest all passed;
the manifest binds the exact successor, schema `0013_web_pin_challenges` and the
current backup digest. The stability observation also passed its 20-minute minimum
uptime/log window and five closing live samples, with stable identities, exact
images, 8/8 services, 7/7 configured healthchecks, zero restart/OOM/severe/exit
signals, healthy readiness/public smoke, firewall persistence, clear interlock and
active timers. Off-host copy, external backup-alert delivery and remote restore
evidence remain open.

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

`scripts/deploy.sh` records the verified revision, actual running repository
digests, and the non-secret authentication boundary (`AUTH_LOGIN_MODE`, the
temporary verifier paths and expiry, and `WHATSAPP_BACKEND`). It validates that
record as private, immutable metadata and compares its
API, worker, scheduler, web, Hermes, Caddy, PostgreSQL and Redis references with
the actual running containers before enabling automatic rollback. A mismatch is a
hard preflight stop. Any pre-deployment failure after a valid record is loaded
atomically restores all nine prior release pointers and the recorded authentication
boundary in `.env.production`. A legacy record without the auth object loads as
the fail-closed disabled boundary; the first compatibility promotion must therefore
be the documented disabled phase.

After migrations begin, a failed later release can restore the prior application
images and rerun smoke. Caddy, PostgreSQL and Redis remain forward-only:
automatic rollback never recreates an older infrastructure image and never reverses
migrations. Only after rollback smoke passes, the script persists a hybrid boundary:
the prior application revision/tag and API/web/Hermes references with the target
infrastructure tag and Caddy/PostgreSQL/backup references. Its separate
`operations_revision` remains the target checkout so subsequent recovery and
promotion use schema-compatible tooling; older records default this field to their
application `revision`. Before recreating rollback services, the coordinator
stops and removes the failed target Caddy and API containers, verifies that neither
container remains, then atomically selects that hybrid image boundary together with the prior auth boundary,
runs the reviewed target release's `auth-secret-init`, and only then recreates the
prior API image. Restoring disabled mode
therefore removes any verifier from the runtime volume even when the target and
previous image digests are identical. If pull, secret initialization, recreation,
or smoke fails, containment removes Caddy and API again before setting the
maintenance interlock; availability is sacrificed rather than leaving the target
login mode reachable. If persistence fails after a healthy prior-boundary recreate,
the maintenance interlock remains and the deployment is not claimed as a verified
rollback. Before the recovery-point backup, the deploy script quiesces every
application writer; if backup or compatibility validation then fails, it restarts
the exact previously running containers. Forward/backward schema compatibility
remains a release requirement. Never run a destructive down-migration during an
outage without a separately reviewed recovery plan.

An unsuccessful guarded promotion automatically restores the recorded auth boundary
before application recreation. Manual temporary-web containment remains fail-closed:
change
`AUTH_LOGIN_MODE` to `disabled`; blank `TEMPORARY_WEB_PIN_VERIFIER`,
`TEMPORARY_WEB_PIN_VERIFIER_FILE`, `TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST`, and
`TEMPORARY_WEB_PIN_EXPIRES_AT`; then promote through the same coordinator. Its
mandatory timestamp also disables request and verification after expiry. Rotating
the shared pilot PIN requires leaving the currently recorded host path selected,
setting a new expiry, and rerunning `scripts/set_temporary_login_pin.sh` at its
hidden prompt. The setter creates a distinct immutable host file and changes only
the host-path setting; the guarded path recreates the secret initializer and API.
Rollback selects the retained prior file before initializer/API recreation, so it
never requires placing a raw PIN in `.env.production` or reconstructing an old PIN.
Retain old versions until a separately reviewed retirement policy proves they are
outside every active, journaled and protected rollback boundary.

The deploy preflight does not promise to contain an already missing or compromised
host verifier before the forward boundary. For that incident, use the runbook's
root-only emergency procedure first: remove Caddy and API, prove both labeled
containers are absent and leave the maintenance interlock active. Treat the site
as deliberately offline until the recorded boundary and recovery plan are
reconciled; do not substitute an ordinary promotion for immediate containment.

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
