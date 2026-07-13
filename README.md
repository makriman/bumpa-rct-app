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
- [Privacy and retention policy](docs/privacy-retention-policy.md)
- [Deployment](docs/deployment.md)
- [Operations runbook](docs/runbook.md)
- [Build-plan compliance ledger](docs/build-plan-compliance.md)
- [Verification ledger](docs/verification.md)
- [Primary references](docs/references.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

## Status

The hardened release gates pass: backend lint/typing plus 353 tests at 85.07%
branch-aware coverage and a separately proven PostgreSQL concurrency test;
frontend lint/typing/build plus 128 unit/component tests with enforced coverage
floors; 26 desktop/mobile Playwright checks on
the pinned Linux image, including Axe, keyboard and visual-regression coverage;
37 host/operations tests; full Docker image and Compose startup; a non-bypass
Postgres RLS probe; and a checksum-verified Postgres/exports/reserved-Hermes backup
and restore drill. `make compose-smoke`
also verifies Postgres-backed OTP login, Bumpa mock sync, chat, research logging
and PDF report download through the same-origin web proxy, then removes every
container and network it created. Production UI data comes from the authenticated
APIs; synthetic fixtures require an explicit demo build and remain visibly
labelled. Treat `docs/verification.md` as the evidence source of truth.
The sealed resilience runner additionally proves exact authenticated chat/sync
budgets, idempotent chat replay, tenant isolation, near-full disk alert
sanitization/signing, 50-event webhook replay, and Redis/Postgres recovery using
synthetic credentials and isolated volumes only.

Release `b35762ab2a9d5c1a4956530cae63040354805510` is live on the five branded
hosts. Core/corrective [PR 27](https://github.com/makriman/bumpa-rct-app/pull/27),
[PR 35](https://github.com/makriman/bumpa-rct-app/pull/35), evidence
[PR 36](https://github.com/makriman/bumpa-rct-app/pull/36), and accessibility
[PR 37](https://github.com/makriman/bumpa-rct-app/pull/37) and delivery-hardening
[PR 41](https://github.com/makriman/bumpa-rct-app/pull/41) are included in the
boundary. Pre-merge PR CI
[29290441375](https://github.com/makriman/bumpa-rct-app/actions/runs/29290441375)
passed 13/13 jobs on release-equivalent tree `5644fd596c1292e3f8c0505fbb80109c4f556bae`;
exact-revision [main CI 29290795169](https://github.com/makriman/bumpa-rct-app/actions/runs/29290795169)
passed 13/13 jobs and [publish run 29291129708](https://github.com/makriman/bumpa-rct-app/actions/runs/29291129708)
passed 7/7 jobs. All eight services are running, all seven configured healthchecks
are healthy, and restart/OOM-kill counts are zero. The production RLS audit passed
23/23 tenant tables at schema `0012_operational_retention` and exercised 115
tenant/table contexts across 670 scoped rows with zero no-context or cross-tenant
leakage. All five branded records are Cloudflare-proxied with Full (strict), HTTPS
enforcement, minimum TLS 1.2 and request-nonce CSP on dynamic documents.

Provider readiness remains deliberately partial. Five mapped durable Bumpa jobs
completed with orders available: stores 1–4 returned accepted-partial 8/10 analytics
datasets, while degraded store 5 returned 7/10 because `products.overview` timed
out/returned HTTP 504. All five mapped Hermes profiles completed live Claude
requests; 40/40 foreign-profile gateway/control attempts were rejected, and an
audited restart plus post-restart completion passed. Read-only Graph checks confirm
that the configured Meta test WABA, phone-number ID and `+15550772716` display
number match. The sender reports `PENDING` and has five approved non-authentication
templates but no authentication template; template creation was denied with Graph
code `10`/subcode `2388185`. The lane remains reply-only with `supports_otp=false`,
and no outbound message was sent. `OFFSITE_BACKUP_SCRIPT` is unset and external
alert delivery is absent. Off-host backup durability, a real alert destination,
provider-complete Bumpa coverage, Meta sender/OTP approval, and privacy/retention
approval remain launch gates.

The live adapters, durable worker/scheduler runtime, transactional outbox, Redis
rate limits, tenant profile lifecycle, and scoped secret mounts are implemented and
contract-tested. The deployable provider-disabled mode remains available for safe
infrastructure verification and never substitutes local mocks for a live provider.
See the verification ledger for the distinction between implemented, canaried,
and production-active capabilities.
