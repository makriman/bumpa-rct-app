# Verification Ledger

This ledger prevents implementation presence from being reported as working
functionality. Status values are `pending`, `local`, `contract`, `live` and
`production`. A higher status requires evidence from that actual environment.

## Evidence rules

- CI uploads JUnit, branch coverage, Playwright reports/traces/screenshots, visual
  diffs, image build provenance and SBOM outputs for 14 days.
- Tests use stable IDs that can be mapped back to acceptance claims.
- Screenshots and fixtures contain synthetic/redacted data only.
- Mock evidence never satisfies live-provider or production infrastructure claims.
- A production claim requires the revision and immutable image digests.

## Current ledger

The statuses below record commands actually executed on 2026-07-12. A present
implementation or aspirational document is not evidence.

| Claim | Required evidence | Current status |
|---|---|---|
| Local Compose renders with only Caddy publishing ports | `docker compose --env-file .env.example config --quiet` and rendered-port assertion | `local` — Compose rendering and the Caddy-only port assertion passed |
| Local environment contract is valid | `scripts/validate_env.sh .env.example local` | `local` — passed |
| Shell scripts parse and pass ShellCheck | `scripts/validate_shell.sh` with ShellCheck installed | `contract` — Bash syntax passed; ShellCheck was unavailable locally and remains pending CI |
| Caddy configuration is valid | Caddy 2.10 validation or a healthy Compose start | `local` — Caddy started in Compose and routed all four local hosts |
| Clean-clone bootstrap is repeatable | fresh runner transcript for `make bootstrap` | `pending` |
| Backend lint, format and strict typing pass | Ruff and mypy commands from `make quality` | `local` — passed |
| Backend required test gate passes | pytest with branch coverage at the configured 85% threshold | `local` — `make quality` passed 22 tests at 90%+ on 2026-07-12 |
| Frontend install, audit, format and typecheck pass | locked install plus the matching npm scripts | `local` — passed |
| Frontend lint, unit coverage and production build pass | the matching npm scripts | `local` — lint/format/typecheck/build and 41 unit tests passed; coverage is reported without an enforced threshold |
| Browser E2E, accessibility and visual checks pass | real Playwright browser run with assertions/artifacts | `contract` — six desktop/mobile Playwright checks passed; axe, keyboard and visual-diff coverage remain pending |
| Local Compose stack boots and cross-surface smoke checks pass | `scripts/compose_smoke.sh` | `local` — fresh images built; Postgres, Redis, API, worker, scheduler, web and Caddy started; all four surface checks passed |
| API migrations succeed on empty Postgres and RLS uses a non-bypass role | backend CI migration plus direct RLS integration JUnit | `local` — explicit migration passed on fresh Postgres; `bumpabestie_app` saw 0 rows without context, 1 tenant with tenant context and 2 with privileged context |
| OTP login is secure and mock OTP is environment-gated | expiry, attempts, consumption, phone/IP rate-limit and production rejection tests | `pending` — happy-path/local tests cover only part of this contract |
| User cannot read or mutate another tenant | negative API, direct RLS and browser tampering tests | `local` — API header isolation and direct non-bypass Postgres RLS probes passed; broader browser tampering remains covered by middleware/API role tests |
| Admin and researcher hosts/routes enforce roles | host/path matrix and Playwright role projects | `contract` — public login routing, tenant-vs-operator middleware authorization and API RBAC tests pass; release builds compile demo mode off |
| Web surfaces use the real local API | browser E2E against FastAPI/Postgres with no canned response path | `local` — `make integration` passed OTP, sync, chat, research event and PDF report flows through the web proxy; settings/admin/research presentation remains partly fixture-backed |
| Bumpa mock normalization is accurate | versioned fixtures, pagination, error and Decimal assertions | `pending` — only basic Decimal/redaction and synthetic sync tests exist |
| Bumpa live sync works | sandbox transcript and redacted canonical/raw reconciliation | `pending` — provider deferred |
| WhatsApp mock verification and core routing work | signed fixture tests, delivery callbacks, retry and fake outbound assertions | `contract` — signature, known/unknown, duplicate, retry, delivery status and STOP/START tests passed |
| WhatsApp live messages/templates work | Meta canary and delivery receipts | `pending` — provider deferred |
| Agent context excludes secrets/PII and isolates profiles | captured mock envelope and cross-profile live canary | `pending` — live runtime deferred |
| Research events and default exports satisfy privacy rules | transaction/failure-path and permission-matrix tests plus artifact scans | `contract` — consent-gated redacted events, role-gated exports and CSV/JSONL/PDF artifacts are tested; formal privacy signoff remains pending |
| Reports produce valid polished artifacts | parser assertions, rendered PDF review and download authorization | `local` — authorized CSV/JSONL/PDF generation and download passed through the running stack; richer chart/report visual QA remains future work |
| Stack handles 50 concurrent inbound events | load report with latency/error/duplicate counts | `pending` |
| Redis/Postgres restart paths preserve correctness | controlled Compose failure test | `pending` |
| Release images are published and portable | successful `publish-images.yml` run, digests, provenance, SBOM and image scan | `pending` — workflow exists; no publish run or image scan evidence yet |
| Backup is durable and restorable | checksum, off-host ID and isolated Postgres/exports/Hermes restore comparison | `local` — checksum verification and same-host Postgres/exports/Hermes restore preserved row/message counts and the stack passed smoke afterward; off-host restore remains pending |
| Production domains have TLS and health | DNS/TLS probes and smoke transcript tied to a release digest | `pending` — Droplet deferred |

## Release decision

A local handoff requires every non-live core product claim through mock-mode E2E
to be at least `local` or `contract`, with no failing required CI check. That bar is
met for the implemented local core; fixture-backed secondary screens and load/failure
drills remain explicit follow-up work. Production launch additionally requires every provider and
infrastructure row to be `live` or `production`, a successful restore drill and an
explicit security review.
