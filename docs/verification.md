# Verification Ledger

This ledger prevents implementation presence from being reported as working
functionality. Status values are `pending`, `local`, `contract`, `live` and
`production`. A higher status requires evidence from that actual environment.

For requirement coverage and the distinct statuses `implemented-tested`,
`production-baseline-disabled`, `deferred-provider` and `external-blocked`, see
`docs/build-plan-compliance.md`.

## Evidence rules

- CI retains backend JUnit/coverage, frontend coverage/Playwright output and local
  image-scan JSON for 14 days. Visual-diff evidence is not implemented. The publish
  workflow attaches provenance/SBOM to registry images and retains each exact
  registry-digest scan report for 30 days.
- Tests use stable IDs that can be mapped back to acceptance claims.
- Screenshots and fixtures contain synthetic/redacted data only.
- Mock evidence never satisfies live-provider or production infrastructure claims.
- A production claim requires the revision and immutable image digests.
- Evidence belongs to the exact tested revision. Editing code after a passing run
  returns affected rows to pending until the relevant gate is rerun.
- A readiness 200 is scoped to the dependencies actually probed by that endpoint.
  The current readiness route queries Postgres and, when the async runtime is
  enabled, requires Redis plus fresh worker/scheduler heartbeats. It reports
  provider selectors but does not call Meta, Bumpa or Hermes.

## Current ledger

The statuses below record commands actually executed on 2026-07-12. Hardened
release `54bb8e9b29295171d65972e094e508d25a7bc53d` is the deployed,
provider-disabled sslip.io baseline. Pull request and CI evidence are
[PR 14](https://github.com/makriman/bumpa-rct-app/pull/14),
[PR CI 29194474957](https://github.com/makriman/bumpa-rct-app/actions/runs/29194474957),
[merged-main CI 29194621699](https://github.com/makriman/bumpa-rct-app/actions/runs/29194621699)
and [publish run 29194814472](https://github.com/makriman/bumpa-rct-app/actions/runs/29194814472).
Post-deployment safety follow-up [PR 15](https://github.com/makriman/bumpa-rct-app/pull/15)
merged as `e128d02c1279c7e6c19b347eafdb3d9884ef0ed5`; its
[main CI 29195530375](https://github.com/makriman/bumpa-rct-app/actions/runs/29195530375)
is green, but it is not the deployed application/image revision. A present
implementation or aspirational document is not evidence.

### Deployed image index references

| Service | Exact GHCR index reference |
|---|---|
| API | `ghcr.io/makriman/bumpabestie-api@sha256:0800d838311705f018456664ad5b161ef9dcb7aaffcceb5c7ce6d0ed6ea02458` |
| Web | `ghcr.io/makriman/bumpabestie-web@sha256:927c7b60db6c78c1f6252b279f897cdd7b2dd1cd16bfc8bcea187386337ed0d5` |
| Caddy | `ghcr.io/makriman/bumpabestie-caddy@sha256:88e705288bc05671d6e6810976c4c195fc7299e420dfa2ef635439c8d8647738` |
| PostgreSQL | `ghcr.io/makriman/bumpabestie-postgres@sha256:e783f3984f0dff9bd4785a0b874d28ddcb4115a16eff6069603990378da55c4e` |
| Backup | `ghcr.io/makriman/bumpabestie-backup@sha256:a102529cdf60e415595ed8a0e1ddbcc73f2d9958c61ff76eedba1f547b5ef0e4` |

| Claim | Required evidence | Current status |
|---|---|---|
| Local Compose renders with only Caddy publishing ports | `docker compose --env-file .env.example config --quiet` and rendered-port assertion | `local` — Compose rendering and the Caddy-only port assertion passed |
| Local environment contract is valid | `scripts/validate_env.sh .env.example local` | `local` — passed |
| Production rejects mock/demo provider configuration | production config unit tests plus `scripts/validate_env.sh <synthetic-env> production` | `local` — typed provider modes, production mock rejection, disabled-mode side-effect assertions and rendered production Compose checks passed |
| Production image/configuration contract is immutable and least-exposed | `scripts/test_production_contract.sh` | `contract` — duplicate/malformed environment entries are rejected; six exact GHCR digests are required, including Hermes; production builds are absent; only Caddy publishes ports; secret mounts and backup/restore capability/network boundaries are asserted |
| Shell scripts parse and pass ShellCheck | `scripts/validate_shell.sh` with ShellCheck installed | `contract` — Bash syntax and ShellCheck passed locally and in exact-release CI 29194621699 |
| Caddy configuration is valid | Caddy 2.11 validation or a healthy Compose start | `production` — exact-release CI validated the patched-Go image; production runs Caddy 2.11.4 built with Go 1.26.5 as UID 10001 under the restricted capability boundary and routes all five hosts |
| Hardened infrastructure images preserve and restore state | `scripts/test_infra_images.sh` and deployment transcript | `production` — exact-release CI proved isolated 16.9-to-16.14 adoption, backup and destructive restore; production upgraded in place to PostgreSQL 16.14, then migrations and readiness passed. Destructive production restore remains intentionally unexercised |
| Clean-clone bootstrap is repeatable | fresh runner transcript for `make bootstrap` | `pending` |
| Backend lint, format and strict typing pass | Ruff and mypy commands from `make quality` | `local` — passed |
| Backend required test gate passes | pytest with branch coverage at the configured 85% threshold | `local` — 111 tests passed at 86.73% branch-aware coverage for the pending release; the historical exact release had 29 tests at 91.33% in CI 29194621699 |
| Frontend install, audit, format and typecheck pass | locked install plus the matching npm scripts | `local` — passed |
| Frontend lint, unit coverage and production build pass | the matching npm scripts | `local` — lint/format/typecheck/build and 78 unit/component tests across 15 files passed for the pending release; coverage is reported without an enforced threshold |
| Browser E2E, accessibility and visual checks pass | real Playwright browser run with assertions/artifacts | `local` — ten desktop/mobile Playwright checks pass; the main flow and responsive navigation were visually/keyboard inspected with accepted screenshots. Automated axe and visual-diff coverage remain pending |
| Local Compose stack boots and cross-surface smoke/integration checks pass | `scripts/compose_smoke.sh` | `local` — fresh images built; migration through `0004`; Postgres, Redis, API, worker, scheduler, web and Caddy healthy; OTP, tenant session, queued Bumpa sync, chat, research event and asynchronous PDF report passed; all containers/networks removed cleanly |
| Provider-disabled production baseline starts only intended services | rendered production Compose, deploy transcript and `docker compose ps` | `production` — release `54bb8e9b29295171d65972e094e508d25a7bc53d` runs exactly Caddy/web/API/Postgres/Redis with zero restarts; worker/scheduler/Hermes are absent |
| Production runtime versions and privilege boundaries match the release | runtime version, UID/capability and restart inspection | `production` — Caddy 2.11.4 built with Go 1.26.5 runs as UID 10001 with restricted capabilities; PostgreSQL is 16.14, Redis is 7.4.9, and all five services have zero restarts |
| Production readiness reports disabled provider state | `/health/ready` response plus database-loss negative test | `production` — the live hardened release returns database `ok` and WhatsApp, Bumpa and agent providers `disabled`; local database-loss behavior is also tested. This is not provider reachability evidence |
| Production public-negative canaries fail closed | production OpenAPI/docs and OTP probes | `production` — API documentation routes are unavailable and a synthetic OTP request returns HTTP 503; no mock OTP or provider response is exposed |
| Provider-dependent production actions fail closed | negative OTP/webhook/chat/sync/report/profile tests with all selectors disabled | `contract` — OTP, webhook, chat, sync, report and profile provisioning reject disabled modes without mock side effects |
| API migrations succeed on empty Postgres and RLS uses a non-bypass role | backend CI migration plus direct RLS integration JUnit | `local` — explicit migration passed on fresh Postgres; `bumpabestie_app` saw 0 rows without context, 1 tenant with tenant context and 2 with privileged context |
| Additive schema-completeness migration is reversible and isolates new tenant tables | SQLite and Postgres 16 upgrade/downgrade/upgrade plus catalog and non-bypass role assertions | `contract` — `0002_schema_completeness` completed both migration cycles; Postgres confirmed INET/nullability and ENABLE+FORCE RLS policies; a NOSUPERUSER/NOBYPASSRLS tenant-a role saw tenant-a rows only; the exact-release CI gate passed |
| OTP login and costly operations are rate constrained | expiry, attempts, consumption, privacy-preserving Redis limits and production rejection tests | `contract` — expiry, single use, cooldown, maximum-attempt lockout, secure cookie, revocation, cookie-origin CSRF and HMAC phone/IP OTP limits are tested; production additionally enforces tenant/user/phone budgets around web chat, WhatsApp chat, Bumpa sync and research reports |
| User cannot read or mutate another tenant | negative API, direct RLS and browser tampering tests | `local` — API header isolation and direct non-bypass Postgres RLS probes passed; broader browser tampering remains covered by middleware/API role tests |
| Admin and researcher hosts/routes enforce roles | host/path matrix and Playwright role projects | `contract` — public login routing, tenant-vs-operator middleware authorization and API RBAC tests pass; release builds compile demo mode off |
| Web surfaces use the real local API | browser E2E against FastAPI/Postgres with no canned response path | `local` — `make compose-smoke` passed OTP, sync, chat, research event and PDF report flows through the web proxy; production user/settings/admin/research views use authenticated APIs, and fixtures require explicitly labelled demo mode |
| Bumpa normalization and failure behavior are accurate | versioned fixtures, pagination, error and Decimal assertions | `contract` — direct/local adapters cover ten datasets plus orders, bounds, retries, Decimal values, unavailable-not-zero semantics, encryption, deep redaction, and durable raw/canonical reconciliation |
| Bumpa live sync works | sandbox transcript and redacted canonical/raw reconciliation | `live` — all five credentials authenticated; four completed every dataset plus orders call, while the fifth passed all except one transient overview timeout. Production tenant sync evidence remains pending |
| WhatsApp verification and durable routing work | signed fixtures, delivery callbacks, retry and outbound assertions | `contract` — signature, known/unknown, dedupe, durable acknowledgement/job processing, retry, delivery status, STOP/START, rate-limit and ambiguous-send tests pass |
| WhatsApp live messages/templates work | Meta canary and delivery receipts | `pending` — token/app/WABA validation and template submissions succeeded, but phone verification/registration, callback subscription, approvals and live receipts remain open |
| Agent context excludes secrets/PII and isolates profiles | captured envelope and cross-profile canary | `contract` — the pinned Hermes image, authenticated private gateways, profile staging/lifecycle, Hermes-only secret and cross-profile isolation pass local contracts; production profile/Claude canaries remain pending |
| Research events and default exports satisfy privacy rules | transaction/failure-path and permission-matrix tests plus artifact scans | `contract` — consent-gated reads/exports, reason-gated raw access, audit logs, keyed pseudonyms, deep redaction, withdrawal invalidation, 24-hour expiry, cleanup and CSV/JSONL scans are tested; formal privacy signoff remains pending |
| Reports produce valid polished artifacts | parser assertions, rendered PDF review and download authorization | `local` — authorized asynchronous CSV/JSONL/PDF generation, expiry and download passed through the running stack; richer chart/report visual QA remains future work |
| Stack handles 50 concurrent inbound events | load report with latency/error/duplicate counts | `pending` |
| Redis/Postgres restart paths preserve correctness | controlled Compose failure test | `pending` |
| Release images are published and portable | successful `publish-images.yml` run, six digests, provenance, SBOM and exact image scans | `production` for the historical release; `contract` for pending — publish run 29194814472 produced the five deployed exact references; the pending contract adds a sixth pinned Hermes image and passes local linux/amd64 and zero-fixable-high/critical scans, awaiting merged-SHA publication |
| Backup is locally restorable | checksum and isolated Postgres/exports/Hermes runtime+staging comparison | `local` — the restricted-capability format-3 backup and isolated restore contract passed for Postgres, exports, Hermes runtime and staging; production still has historical format-2 backup evidence and no off-host restore |
| Production local backup and schedule are operational | backup manifest/checksum, release/image match and systemd timer state | `production` — backup `20260712T140353Z` passed SHA-256 verification with manifest format 2, release `54bb8e9b29295171d65972e094e508d25a7bc53d` and the exact backup image reference; `--no-deps` inspection left the running PostgreSQL container unchanged. The timer is enabled with next run `2026-07-13 02:32 UTC` |
| Backup is off-host durable | remote object ID/checksum, failure alert and restore on an isolated host | `pending` — no off-host provider/credential or verified handoff exists; a green timer alone is insufficient |
| Production host is accessible and hardened | SSH fingerprint, non-root login, OS/firewall/listening-port transcript | `production` — Ubuntu 24.04.4 host `165.227.228.20`; exact ED25519 key accepted for root and deploy user; Docker 29.6.1/Compose 5.3.1, UFW, fail2ban, unattended upgrades and 2 GB swap verified; the provider-disabled baseline is installed |
| Production domains have TLS and health | DNS/TLS probes and smoke transcript tied to a release digest | `production` — release `54bb8e9b29295171d65972e094e508d25a7bc53d` serves valid TLS and healthy routing on `bumpabestie.165-227-228-20.sslip.io` plus the `www`, `api`, `admin` and `research` subdomains; `bumpabestie.com` remains unregistered and blocked for launch |

## Release decision

A local handoff requires every non-live core product claim through mock-mode E2E to
be at least `local` or `contract`, with no failing required CI check on the exact
handoff revision. Hardened release `54bb8e9b29295171d65972e094e508d25a7bc53d`
met that bar and its exact-revision CI, publication and infrastructure deployment
gates passed.

The provider-disabled deployment is an infrastructure verification stage only. It
may not serve SMEs or researchers. Production launch additionally requires every
provider and infrastructure row to be `live` or `production`, a restore from the
off-host copy, explicit privacy/security approval and completion of the launch
checklist in `docs/build-plan-compliance.md`.
