# Bumpa Bestie

Bumpa Bestie is a multi-tenant, research-instrumented AI business assistant for
SMEs using Bumpa. The user experience spans web chat, an operations console and a
permissioned research portal. FastAPI is the control plane; Next.js provides the
four host-routed surfaces; Postgres provides durable state and Redis provides the
coordination foundation for queued work.

The repository supports credential-free local development through deterministic
mock adapters and production adapters for WhatsApp Cloud API, Bumpa, and
Claude-through-Hermes. Production activation remains an evidence gate: a provider
is not considered live until its credentials, external account state, canary, and
operating checks pass for the exact release.

## Local quick start

Requirements: Docker 29+, Docker Compose v2+, Node 22, Python 3.12, `uv`, GNU
Make, `curl`, and `jq`.

```bash
cp .env.example .env
make bootstrap
make dev
make smoke
```

Local hosts are served by Caddy on port 8080:

- `http://bumpabestie.localhost:8080`
- `http://admin.bumpabestie.localhost:8080`
- `http://research.bumpabestie.localhost:8080`
- `http://api.bumpabestie.localhost:8080/health`

Run the intended local quality gate with `make quality`. A non-zero result is a
release blocker, not a warning. See
[`docs/integration.md`](docs/integration.md) for mock-provider behavior and
[`docs/verification.md`](docs/verification.md) for the claim-by-claim evidence
ledger.

## Repository map

```text
apps/api       FastAPI control plane, worker and scheduler
apps/web       Next.js public, user, admin and research surfaces
infra          Caddy, backup image and host service definitions
scripts        Local bootstrap, validation, deployment and operations
docs           Architecture, security, integration and operating guidance
tests          Cross-service fixtures and system-test documentation
```

## Security boundary

Only Caddy publishes host ports. API, worker and Hermes services attach to
an explicit egress network for provider access; Postgres and Redis remain on a
private data network. Provider secrets are server-side only. Production startup
uses `.env.production`, stored on the host with mode `0600`, and fails when required
secrets retain local defaults.

## Documentation

- [Architecture](docs/architecture.md)
- [Development and integrations](docs/integration.md)
- [Security model](docs/security.md)
- [Deployment](docs/deployment.md)
- [Operations runbook](docs/runbook.md)
- [Build-plan compliance ledger](docs/build-plan-compliance.md)
- [Verification ledger](docs/verification.md)
- [Primary references](docs/references.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

## Status

The local gates pass: backend lint/typing plus 324 tests at 85.02% branch-aware
coverage, frontend lint/typing/build plus 121 unit/component tests across 22 files,
eighteen desktop/mobile Playwright checks, 37 host/operations tests, full Docker
image and Compose startup, a non-bypass Postgres RLS probe, and a checksum-verified
Postgres/exports/reserved-Hermes backup and restore drill. `make compose-smoke`
also verifies Postgres-backed OTP login, Bumpa mock sync, chat, research logging
and PDF report download through the same-origin web proxy, then removes every
container and network it created. Production UI data comes from the authenticated
APIs; synthetic fixtures require an explicit demo build and remain visibly
labelled. Treat `docs/verification.md` as the evidence source of truth.
The sealed resilience runner additionally proves exact authenticated chat/sync
budgets, idempotent chat replay, tenant isolation, near-full disk alert
sanitization/signing, 50-event webhook replay, and Redis/Postgres recovery using
synthetic credentials and isolated volumes only.

Release `6fbe2a9eb0591bde5ad3cebe94d8f3568075df7b` is live on the five branded
hosts. Core/corrective [PR 27](https://github.com/makriman/bumpa-rct-app/pull/27),
[PR 35](https://github.com/makriman/bumpa-rct-app/pull/35), evidence
[PR 36](https://github.com/makriman/bumpa-rct-app/pull/36), and accessibility
[PR 37](https://github.com/makriman/bumpa-rct-app/pull/37) are included in the
boundary. Exact-revision [main CI 29274276654](https://github.com/makriman/bumpa-rct-app/actions/runs/29274276654)
passed 13/13 jobs and [publish run 29274700347](https://github.com/makriman/bumpa-rct-app/actions/runs/29274700347)
passed 7/7 jobs. All eight services are running, all seven configured healthchecks
are healthy, and restart/OOM-kill counts are zero. The production RLS audit passed
23/23 tenant tables and exercised 115 tenant/table contexts across 516 scoped rows
with zero no-context or cross-tenant leakage.

Provider readiness remains deliberately partial. Five mapped durable Bumpa jobs
completed with orders available: stores 1–4 returned accepted-partial 8/10 analytics
datasets, while degraded store 5 returned 7/10 because `products.overview` timed
out/returned HTTP 504. All five mapped Hermes profiles completed a live Claude
request, but cross-profile attack and recovery canaries remain open. The configured
Meta test WABA is subscribed to the app and its configured sender phone-number ID
is validated, but the WABA has zero authentication templates;
template creation was denied with Graph code
`10`/subcode `2388185`. The lane remains reply-only with `supports_otp=false`, and
no outbound message was sent. Off-host backup durability, a real alert destination,
and privacy/retention approval remain launch gates.

The live adapters, durable worker/scheduler runtime, transactional outbox, Redis
rate limits, tenant profile lifecycle, and scoped secret mounts are implemented and
contract-tested. The deployable provider-disabled mode remains available for safe
infrastructure verification and never substitutes local mocks for a live provider.
See the verification ledger for the distinction between implemented, canaried,
and production-active capabilities.
