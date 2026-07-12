# CODEX.md

## Project

Bumpa Bestie is a research-instrumented SME assistant built with Next.js, FastAPI,
Postgres and Redis. FastAPI owns identity, authorization, tenant scope and external
provider routing. External WhatsApp, Bumpa and agent providers are adapters; local
development must work with deterministic mock adapters and no credentials.

## Non-negotiables

- Codex is a local development tool only and is never a runtime service.
- Never commit secrets, production payloads, customer PII or credentials.
- Every tenant-owned query is tenant-scoped and tenant isolation is tested against
  Postgres using a role that cannot bypass RLS.
- The browser calls only the application API. It never receives provider keys.
- Hermes receives compact tenant-scoped context and never receives Bumpa keys.
- Money uses `Decimal` or integer minor units, never binary floating point.
- Unknown upstream values are preserved; unavailable data is never silently zero.
- Admin and raw-data access is explicit, least privilege and audit logged.
- Logs and research exports redact PII by default.
- App containers have explicit outbound egress but only Caddy publishes host ports.

## Quality contract

Run `make quality` before handing off changes. Schema changes require migrations,
tests and documentation. User-visible changes require E2E, accessibility and visual
evidence. Do not claim live WhatsApp, Bumpa, Claude/Hermes or Droplet functionality
from mock tests; record those as pending in `docs/verification.md`.

## Common commands

```text
make bootstrap
make dev
make lint
make typecheck
make test
make e2e
make compose-config
make compose-smoke
make quality
```
