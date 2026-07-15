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
- [Temporary web-only login](docs/temporary-web-login.md)
- [Operations runbook](docs/runbook.md)
- [Build-plan compliance ledger](docs/build-plan-compliance.md)
- [Verification ledger](docs/verification.md)
- [Primary references](docs/references.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

## Status

Application release `c0c15443352ab84fde1d2edfde1ed0692ed842f6` from
[PR 52](https://github.com/makriman/bumpa-rct-app/pull/52) is live at
[bumpabestie.com](https://bumpabestie.com) and the four companion hosts. Exact-main
[CI 29412671738](https://github.com/makriman/bumpa-rct-app/actions/runs/29412671738)
passed 13/13 jobs, and
[publication 29413085773](https://github.com/makriman/bumpa-rct-app/actions/runs/29413085773)
passed 7/7 jobs for all six immutable images. Production runs eight services at
schema `0015_bumpa_store_context`; all seven configured healthchecks pass and
Cloudflare remains the only web ingress path.

The release fixes the Bumpa store-context adapter and persistence boundary. All
five mapped connections retain store-local range, currency, raw evidence, metric
snapshots, canonical orders and items. Four stores complete as accepted partial
with eight available analytics datasets and the provider's two typed profit
limitations. The remaining store completes durably as degraded only because the
provider does not answer `products.overview` inside the scoped 90-second policy;
orders and the other seven available datasets remain usable. Missing or failed
provider values are never represented as zero.

Five isolated Hermes profiles each completed a live Claude request from a synthetic
prompt; normal tenant-scoped redacted context stayed inside Hermes and response
bodies were omitted from evidence. All 20 cross-profile gateway credential attempts
were rejected. The complete temporary-login
matrix also passed: the five approved mapped collaborators reached public chat,
administration and research in all 15 authorized combinations, host-only cookies
did not cross sibling domains, generic denials did not reveal mapping state, logout
revoked every session, and cleanup left no active session or challenge. WhatsApp
authentication and delivery remain deliberately parked.

The public product now has the Bumpa Bestie brand system, responsive country-code
picker, route-specific canonical/Open Graph/Twitter metadata, homepage JSON-LD,
robots and sitemap policy, PWA manifest, favicons, and explicit no-index headers on private
surfaces. The exact release passed the complete local gate: 483 API tests (one
skipped) at 85.75% branch coverage, 171 web tests, 79 operations tests, lint,
format, strict typing, migration, generated-contract, container, browser and
security checks.

WhatsApp activation, the provider-side `products.overview` timeout on one store,
encrypted off-host backup with isolated restore, external alert delivery, and
formal privacy/security/retention approval remain explicit gates. The canonical
redacted release record is
[`docs/release-evidence-c0c1544.md`](docs/release-evidence-c0c1544.md); use
[`docs/verification.md`](docs/verification.md) for the evidence ledger and
[`docs/temporary-web-login.md`](docs/temporary-web-login.md) for the contained
login boundary.
