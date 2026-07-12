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

The statuses below record commands actually executed on 2026-07-12. Release
`41935d67696fee45b184a65c0a9bf39e0708ae89` is deployed on the temporary
sslip.io hosts. [PR 19](https://github.com/makriman/bumpa-rct-app/pull/19),
[merged-main CI 29205303835](https://github.com/makriman/bumpa-rct-app/actions/runs/29205303835)
and [publish run 29205487124](https://github.com/makriman/bumpa-rct-app/actions/runs/29205487124)
are green for that exact revision. Production runs all eight services with
`meta`, `bumpa` and `hermes` selected. Provider selection is not itself live
provider proof; the provider-specific rows below preserve that distinction.

### Deployed image index references

| Service | Exact GHCR index reference |
|---|---|
| API/worker/scheduler | `ghcr.io/makriman/bumpabestie-api@sha256:0d669e809dafb103455770ea9733c3a6d9ae6601601771cb760e3008b6a77ac6` |
| Web | `ghcr.io/makriman/bumpabestie-web@sha256:ee0afcb024ebd2c57862e95385889754a1ac1dea03f2daa2961c0647b131433e` |
| Caddy | `ghcr.io/makriman/bumpabestie-caddy@sha256:24e5d48f58ecf4387a1c8073ba2843032bd468f36ef2f0bedf6e4c09e29ab3c4` |
| PostgreSQL | `ghcr.io/makriman/bumpabestie-postgres@sha256:c572a457de73e3796bdf8319c8d9e1fd0176a3ab4dd561b5bd6ab676c1e277e5` |
| Backup | `ghcr.io/makriman/bumpabestie-backup@sha256:3edb86fb562ee58b732f827a280e1c3750a1bbcf5dd5b15b863c0d5f986a32c2` |
| Hermes | `ghcr.io/makriman/bumpabestie-hermes@sha256:33b1898d816faf13a312f97ed1d7844d3a73591fcd05c14ab508de24fd659922` |

| Claim | Required evidence | Current status |
|---|---|---|
| Local Compose renders with only Caddy publishing ports | `docker compose --env-file .env.example config --quiet` and rendered-port assertion | `local` — Compose rendering and the Caddy-only port assertion passed |
| Local environment contract is valid | `scripts/validate_env.sh .env.example local` | `local` — passed |
| Production rejects mock/demo provider configuration | production config unit tests plus `scripts/validate_env.sh <synthetic-env> production` | `local` — typed provider modes, production mock rejection, disabled-mode side-effect assertions and rendered production Compose checks passed |
| Production image/configuration contract is immutable and least-exposed | `scripts/test_production_contract.sh` | `contract` — duplicate/malformed environment entries are rejected; six exact GHCR digests are required, including Hermes; production builds are absent; only Caddy publishes ports; secret mounts and backup/restore capability/network boundaries are asserted |
| Shell scripts parse and pass ShellCheck | `scripts/validate_shell.sh` with ShellCheck installed | `production` — Bash syntax and ShellCheck passed locally and in exact-release CI 29205303835 |
| Caddy configuration is valid | Caddy 2.11 validation or a healthy Compose start | `production` — exact-release CI validated the patched-Go image; production runs Caddy 2.11.4 built with Go 1.26.5 as UID 10001 under the restricted capability boundary and routes all five hosts |
| Hardened infrastructure images preserve and restore state | `scripts/test_infra_images.sh` and deployment transcript | `production` — exact-release CI proved isolated 16.9-to-16.14 adoption, backup and destructive restore; production upgraded in place to PostgreSQL 16.14, then migrations and readiness passed. Destructive production restore remains intentionally unexercised |
| Clean-clone bootstrap is repeatable | fresh runner transcript for `make bootstrap` | `pending` |
| Backend lint, format and strict typing pass | Ruff and mypy commands from `make quality` | `local` — passed |
| Backend required test gate passes | pytest with branch coverage at the configured 85% threshold | `production` — 113 tests passed at 86.82% branch-aware coverage locally and the exact revision passed merged-main CI 29205303835 |
| Frontend install, audit, format and typecheck pass | locked install plus the matching npm scripts | `local` — passed |
| Frontend lint, unit coverage and production build pass | the matching npm scripts | `local` — lint/format/typecheck/build and 78 unit/component tests across 15 files passed for the pending release; coverage is reported without an enforced threshold |
| Browser E2E, accessibility and visual checks pass | real Playwright browser run with assertions/artifacts | `local` — ten desktop/mobile Playwright checks pass; the main flow and responsive navigation were visually/keyboard inspected with accepted screenshots. Automated axe and visual-diff coverage remain pending |
| Local Compose stack boots and cross-surface smoke/integration checks pass | `scripts/compose_smoke.sh` | `local` — fresh images built; migration through `0004`; Postgres, Redis, API, worker, scheduler, web and Caddy healthy; OTP, tenant session, queued Bumpa sync, chat, research event and asynchronous PDF report passed; all containers/networks removed cleanly |
| Production starts only intended services | rendered production Compose, deploy transcript and `docker compose ps` | `production` — release `41935d67696fee45b184a65c0a9bf39e0708ae89` runs Caddy, web, API, worker, scheduler, Hermes, PostgreSQL and Redis; all eight have zero restarts and zero OOM kills |
| Production runtime versions and privilege boundaries match the release | runtime version, UID/capability and restart inspection | `production` — Caddy 2.11.4 built with Go 1.26.5 runs as UID 10001 with restricted capabilities; PostgreSQL is 16.14, Redis is 7.4.9, and no private service port is published |
| Production readiness reports intended provider state | `/health/ready` response plus dependency negative tests | `production` — database, Redis, worker and scheduler are `ok`; selectors report WhatsApp `meta`, Bumpa `bumpa` and agent `hermes`. This is not provider reachability evidence |
| Production public-negative canaries fail closed | production OpenAPI/docs and OTP probes | `production` — API documentation routes are unavailable and a synthetic OTP request returns HTTP 503; no mock OTP or provider response is exposed |
| Provider-dependent production actions fail closed | negative OTP/webhook/chat/sync/report/profile tests with all selectors disabled | `contract` — OTP, webhook, chat, sync, report and profile provisioning reject disabled modes without mock side effects |
| API migrations succeed on empty Postgres and RLS uses a non-bypass role | backend CI migration plus direct RLS integration JUnit | `local` — explicit migration passed on fresh Postgres; `bumpabestie_app` saw 0 rows without context, 1 tenant with tenant context and 2 with privileged context |
| Additive schema-completeness migration is reversible and isolates new tenant tables | SQLite and Postgres 16 upgrade/downgrade/upgrade plus catalog and non-bypass role assertions | `contract` — `0002_schema_completeness` completed both migration cycles; Postgres confirmed INET/nullability and ENABLE+FORCE RLS policies; a NOSUPERUSER/NOBYPASSRLS tenant-a role saw tenant-a rows only; the exact-release CI gate passed |
| OTP login and costly operations are rate constrained | expiry, attempts, consumption, privacy-preserving Redis limits and production rejection tests | `contract` — expiry, single use, cooldown, maximum-attempt lockout, secure cookie, revocation, cookie-origin CSRF and HMAC phone/IP OTP limits are tested; production additionally enforces tenant/user/phone budgets around web chat, WhatsApp chat, Bumpa sync and research reports |
| User cannot read or mutate another tenant | negative API, direct RLS and browser tampering tests | `local` — API header isolation and direct non-bypass Postgres RLS probes passed; broader browser tampering remains covered by middleware/API role tests |
| Admin and researcher hosts/routes enforce roles | host/path matrix and Playwright role projects | `contract` — public login routing, tenant-vs-operator middleware authorization and API RBAC tests pass; release builds compile demo mode off |
| Web surfaces use the real local API | browser E2E against FastAPI/Postgres with no canned response path | `local` — `make compose-smoke` passed OTP, sync, chat, research event and PDF report flows through the web proxy; production user/settings/admin/research views use authenticated APIs, and fixtures require explicitly labelled demo mode |
| Bumpa normalization and failure behavior are accurate | versioned fixtures, pagination, error and Decimal assertions | `contract` — direct/local adapters cover ten datasets plus orders, bounds, retries, Decimal values, unavailable-not-zero semantics, encryption, deep redaction, and durable raw/canonical reconciliation |
| Bumpa live sync works | sandbox transcript and redacted canonical/raw reconciliation | `live` — all five credentials authenticate and pass the provider probe. Production tenant sync/reconciliation remains pending until the five tenant mappings are onboarded |
| WhatsApp verification and durable routing work | signed fixtures, delivery callbacks, retry and outbound assertions | `contract` — signature, known/unknown, dedupe, durable acknowledgement/job processing, retry, delivery status, STOP/START, rate-limit and ambiguous-send tests pass |
| WhatsApp live messages/templates work | Meta canary and delivery receipts | `live` for ingress — phone is verified, Cloud API connected, app and WABA callbacks are active, the valid/invalid challenge paths pass, and a correctly signed public webhook was processed. Business verification is still `not_verified`; `bb_otp_login` is absent and six custom templates are pending, so outbound/OTP receipts remain open |
| Agent context excludes secrets/PII and isolates profiles | captured envelope and cross-profile canary | `contract` — the pinned Hermes image, authenticated private gateways, profile staging/lifecycle, Hermes-only secret and cross-profile isolation pass local contracts; production profile/Claude canaries remain pending |
| Research events and default exports satisfy privacy rules | transaction/failure-path and permission-matrix tests plus artifact scans | `contract` — consent-gated reads/exports, reason-gated raw access, audit logs, keyed pseudonyms, deep redaction, withdrawal invalidation, 24-hour expiry, cleanup and CSV/JSONL scans are tested; formal privacy signoff remains pending |
| Reports produce valid polished artifacts | parser assertions, rendered PDF review and download authorization | `local` — authorized asynchronous CSV/JSONL/PDF generation, expiry and download passed through the running stack; richer chart/report visual QA remains future work |
| Stack handles 50 concurrent inbound events | load report with latency/error/duplicate counts | `pending` |
| Redis/Postgres restart paths preserve correctness | controlled Compose failure test | `pending` |
| Release images are published and portable | successful `publish-images.yml` run, six digests, provenance, SBOM and exact image scans | `production` — publish run 29205487124 published and scanned all six exact release image references for revision `41935d67696fee45b184a65c0a9bf39e0708ae89` |
| Backup is locally restorable | checksum and isolated Postgres/exports/Hermes runtime+staging comparison | `production` — the restricted-capability format-3 backup and isolated restore contract passed for PostgreSQL, exports, Hermes runtime and staging; production remains without an off-host restore |
| Production local backup and schedule are operational | backup manifest/checksum, release/image match and systemd timer state | `production` — backup `20260712T195838Z` passed all five SHA-256 checks; its three archives and 252-entry PostgreSQL dump parse; manifest format 3 matches the exact release, schema `0004_provider_delivery`, PostgreSQL 16.14 and backup digest. The timer is enabled and the last scheduled result succeeded |
| Backup is off-host durable | remote object ID/checksum, failure alert and restore on an isolated host | `pending` — no off-host provider/credential or verified handoff exists; a green timer alone is insufficient |
| Production host is accessible and hardened | SSH fingerprint, non-root login, OS/firewall/listening-port transcript | `production` — Ubuntu 24.04.4 host `165.227.228.20`; exact ED25519 key accepted for root and deploy user; Docker 29.6.1/Compose 5.3.1, UFW, fail2ban, unattended upgrades and 2 GB swap verified |
| Production domains have TLS and health | DNS/TLS probes and smoke transcript tied to a release digest | `production` — release `41935d67696fee45b184a65c0a9bf39e0708ae89` serves valid TLS and healthy routing on `bumpabestie.165-227-228-20.sslip.io` plus the `www`, `api`, `admin` and `research` subdomains; `bumpabestie.com` has no DNS and remains blocked for branded launch |

## Release decision

A local handoff requires every non-live core product claim through mock-mode E2E to
be at least `local` or `contract`, with no failing required CI check on the exact
handoff revision. Release `41935d67696fee45b184a65c0a9bf39e0708ae89` met
that bar and its exact-revision CI, publication and infrastructure deployment gates
passed.

The runtime is live but is not yet authorized for real SME onboarding. Production
launch still requires a dedicated global operator, five tenant/profile onboarding
canaries, Meta business verification plus approved authentication/operational
templates, a delivered outbound receipt, branded DNS, an off-host restore, and
explicit privacy/security approval.
