# Bumpa Bestie

Bumpa Bestie is a multi-tenant, research-instrumented AI business assistant for
SMEs using Bumpa. The user experience spans web chat, an operations console and a
permissioned research portal. FastAPI is the control plane; Next.js provides the
four host-routed surfaces; Postgres provides durable state and Redis provides the
coordination foundation for queued work.

The repository currently supports credential-free local development through
deterministic mock adapters. Live WhatsApp Cloud API, Bumpa, Claude/Hermes and
DigitalOcean activation are deliberately separate deployment gates.

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

Only Caddy publishes host ports. API, worker and future Hermes services attach to
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

The local gates pass: backend lint/typing plus 29 tests at 91%+ branch-aware
coverage, frontend lint/typing/build plus 52 unit/component tests, eight
desktop/mobile Playwright checks, full Docker image and Compose startup, a
non-bypass Postgres RLS probe, and a checksum-verified
Postgres/exports/reserved-Hermes backup and restore drill. `make compose-smoke`
also verifies Postgres-backed OTP login, Bumpa mock sync, chat, research logging
and PDF report download through the same-origin web proxy, then removes every
container and network it created. Production UI data comes from the authenticated
APIs; synthetic fixtures require an explicit demo build and remain visibly
labelled. Treat `docs/verification.md` as the evidence source of truth.

Live Bumpa, WhatsApp, and Claude-through-Hermes claims are separately deferred until
their credentials, adapters, and verification rows are complete. The deployable
provider-disabled baseline never substitutes local mocks for those integrations.
