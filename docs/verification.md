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
  The current readiness route queries Postgres and reports provider selectors; it
  does not call Redis, Meta, Bumpa or Hermes.

## Current ledger

The statuses below record commands actually executed on 2026-07-12. Revision
`1929771abe932dfd44aad6763e1f5caff19fa833` is the already-live, provider-disabled
sslip.io baseline. The hardened five-image candidate has fresh local evidence but is
not bound to a release SHA until it is committed and CI passes that exact revision.
A present implementation or aspirational document is not evidence.

| Claim | Required evidence | Current status |
|---|---|---|
| Local Compose renders with only Caddy publishing ports | `docker compose --env-file .env.example config --quiet` and rendered-port assertion | `local` — Compose rendering and the Caddy-only port assertion passed |
| Local environment contract is valid | `scripts/validate_env.sh .env.example local` | `local` — passed |
| Production rejects mock/demo provider configuration | production config unit tests plus `scripts/validate_env.sh <synthetic-env> production` | `local` — typed provider modes, production mock rejection, disabled-mode side-effect assertions and rendered production Compose checks passed |
| Production image/configuration contract is immutable and least-exposed | `scripts/test_production_contract.sh` | `contract` — duplicate/malformed environment entries are rejected; five exact GHCR digests are required; production builds are absent; only Caddy publishes ports; backup/restore use only the data network and separate capability sets |
| Shell scripts parse and pass ShellCheck | `scripts/validate_shell.sh` with ShellCheck installed | `contract` — Bash syntax and pinned-container ShellCheck passed locally; final-SHA CI remains required |
| Caddy configuration is valid | Caddy 2.11 validation or a healthy Compose start | `local` — the patched-Go Caddy image validates the production config; Compose routed public, `www`, API, admin and research hosts |
| Hardened infrastructure images preserve and restore state | `scripts/test_infra_images.sh` | `local` — PostgreSQL 16.14 adopted a gracefully stopped 16.9 volume and preserved its row/role; restricted-capability backup produced checksum-verified manifest format 2; isolated restore reset `public`, removed a newer-only object, restored exports/Hermes artifacts and reapplied the non-superuser/non-bypass application role; Caddy 2.11.4 built with Go 1.26.5 ran as UID 10001 under its production capability boundary |
| Clean-clone bootstrap is repeatable | fresh runner transcript for `make bootstrap` | `pending` |
| Backend lint, format and strict typing pass | Ruff and mypy commands from `make quality` | `local` — passed |
| Backend required test gate passes | pytest with branch coverage at the configured 85% threshold | `local` — 29 tests passed at 91.33% branch-aware coverage, pending binding to the final commit SHA |
| Frontend install, audit, format and typecheck pass | locked install plus the matching npm scripts | `local` — passed |
| Frontend lint, unit coverage and production build pass | the matching npm scripts | `local` — lint/format/typecheck/build and 52 unit/component tests passed; coverage is reported without an enforced threshold |
| Browser E2E, accessibility and visual checks pass | real Playwright browser run with assertions/artifacts | `contract` — eight desktop/mobile Playwright checks passed; axe, keyboard and visual-diff coverage remain pending |
| Local Compose stack boots and cross-surface smoke/integration checks pass | `scripts/compose_smoke.sh` | `local` — fresh images built; migration through `0002`; Postgres, Redis, API, worker, scheduler, web and Caddy healthy; six surface checks plus OTP, tenant session, Bumpa sync, chat, research event and PDF report passed; all containers/networks removed cleanly |
| Provider-disabled production baseline starts only intended services | rendered production Compose, deploy transcript and `docker compose ps` | `production` — revision `1929771abe932dfd44aad6763e1f5caff19fa833` runs exactly Caddy/web/API/Postgres/Redis on the sslip.io baseline; worker/scheduler/Hermes are absent. The hardened candidate is not deployed yet |
| Production readiness reports disabled provider state | `/health/ready` response plus database-loss negative test | `production` — the live `1929771abe932dfd44aad6763e1f5caff19fa833` baseline returns database `ok` and all three providers `disabled`; local database-loss behavior is also tested. This is not provider reachability evidence |
| Provider-dependent production actions fail closed | negative OTP/webhook/chat/sync/report/profile tests with all selectors disabled | `contract` — OTP, webhook, chat, sync, report and profile provisioning reject disabled modes without mock side effects |
| API migrations succeed on empty Postgres and RLS uses a non-bypass role | backend CI migration plus direct RLS integration JUnit | `local` — explicit migration passed on fresh Postgres; `bumpabestie_app` saw 0 rows without context, 1 tenant with tenant context and 2 with privileged context |
| Additive schema-completeness migration is reversible and isolates new tenant tables | SQLite and Postgres 16 upgrade/downgrade/upgrade plus catalog and non-bypass role assertions | `local` — `0002_schema_completeness` completed both migration cycles; Postgres confirmed INET/nullability and ENABLE+FORCE RLS policies; a NOSUPERUSER/NOBYPASSRLS tenant-a role saw tenant-a rows only; final SHA binding remains pending |
| OTP login is secure and mock OTP is environment-gated | expiry, attempts, consumption, phone/IP rate-limit and production rejection tests | `pending` — single use, cooldown, maximum-attempt lockout, secure cookie, token revocation and production mock rejection are tested; explicit expiry and real IP/Redis-backed rate limiting remain open |
| User cannot read or mutate another tenant | negative API, direct RLS and browser tampering tests | `local` — API header isolation and direct non-bypass Postgres RLS probes passed; broader browser tampering remains covered by middleware/API role tests |
| Admin and researcher hosts/routes enforce roles | host/path matrix and Playwright role projects | `contract` — public login routing, tenant-vs-operator middleware authorization and API RBAC tests pass; release builds compile demo mode off |
| Web surfaces use the real local API | browser E2E against FastAPI/Postgres with no canned response path | `local` — `make compose-smoke` passed OTP, sync, chat, research event and PDF report flows through the web proxy; production user/settings/admin/research views use authenticated APIs, and fixtures require explicitly labelled demo mode |
| Bumpa mock normalization is accurate | versioned fixtures, pagination, error and Decimal assertions | `pending` — only basic Decimal/redaction and synthetic sync tests exist |
| Bumpa live sync works | sandbox transcript and redacted canonical/raw reconciliation | `pending` — provider deferred |
| WhatsApp mock verification and core routing work | signed fixture tests, delivery callbacks, retry and fake outbound assertions | `contract` — signature, known/unknown, duplicate, retry, delivery status and STOP/START tests passed |
| WhatsApp live messages/templates work | Meta canary and delivery receipts | `pending` — provider deferred |
| Agent context excludes secrets/PII and isolates profiles | captured mock envelope and cross-profile live canary | `pending` — live runtime deferred |
| Research events and default exports satisfy privacy rules | transaction/failure-path and permission-matrix tests plus artifact scans | `contract` — consent-gated reads/exports, keyed domain-separated pseudonyms, defensive legacy-row re-redaction, deep structured redaction and CSV/JSONL artifact scans are tested; formal privacy signoff remains pending |
| Reports produce valid polished artifacts | parser assertions, rendered PDF review and download authorization | `local` — authorized CSV/JSONL/PDF generation and download passed through the running stack; richer chart/report visual QA remains future work |
| Stack handles 50 concurrent inbound events | load report with latency/error/duplicate counts | `pending` |
| Redis/Postgres restart paths preserve correctness | controlled Compose failure test | `pending` |
| Release images are published and portable | successful `publish-images.yml` run, five digests, provenance, SBOM and exact image scans | `pending` — the prior baseline API/web images were published, and hardened infrastructure runtimes have zero local fixable critical/high findings; the final five-image candidate has not been published or scanned by exact registry digest |
| Backup is locally restorable | checksum and isolated Postgres/exports/reserved-Hermes-volume comparison | `local` — the restricted-capability format-2 backup and isolated restore contract passed, preserving database state and replacing stale exports/reserved-Hermes contents; production restore remains intentionally unexercised |
| Backup is off-host durable | remote object ID/checksum, failure alert and restore on an isolated host | `pending` — no off-host provider/credential or verified handoff exists; a green timer alone is insufficient |
| Production host is accessible and hardened | SSH fingerprint, non-root login, OS/firewall/listening-port transcript | `production` — Ubuntu 24.04.4 host `165.227.228.20`; exact ED25519 key accepted for root and deploy user; Docker 29.6.1/Compose 5.3.1, UFW, fail2ban, unattended upgrades and 2 GB swap verified; the provider-disabled baseline is installed |
| Production domains have TLS and health | DNS/TLS probes and smoke transcript tied to a release digest | `production` — revision `1929771abe932dfd44aad6763e1f5caff19fa833` served valid TLS and healthy routing on `bumpabestie.165-227-228-20.sslip.io`, `www`, `api`, `admin` and `research` host variants; `bumpabestie.com` remains unregistered and blocked for launch |

## Release decision

A local handoff requires every non-live core product claim through mock-mode E2E to
be at least `local` or `contract`, with no failing required CI check on the exact
handoff revision. The prior baseline met that bar for its implemented local core;
the final hardening revision must rerun it.

The provider-disabled deployment is an infrastructure verification stage only. It
may not serve SMEs or researchers. Production launch additionally requires every
provider and infrastructure row to be `live` or `production`, a restore from the
off-host copy, explicit privacy/security approval and completion of the launch
checklist in `docs/build-plan-compliance.md`.
