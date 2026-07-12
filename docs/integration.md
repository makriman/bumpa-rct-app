# Development and Integration Contracts

## Current implementation boundary

This file describes the provider and end-to-end contracts. The in-process commerce,
messaging, classifier and agent mocks cover the deterministic local path, including
webhook retry and delivery status tests. `make integration` exercises Postgres-backed
OTP, commerce sync, chat, research logging and PDF reports through the same-origin
Next.js proxy. Redis-backed queues/rate limits, generated OpenAPI clients/MSW
handlers and full browser E2E against every API surface remain unimplemented.
Settings/admin/research screens remain partly synthetic previews, and an explicit
demo-cookie path remains for credential-free local UI testing. Live providers are
intentionally deferred.

## Local mode

Local development is credential free. Copy `.env.example` to `.env`, then run
`make bootstrap` and `make dev`. The application must refuse to initialize a live
adapter when its required credentials are absent, and must refuse every mock adapter
when `APP_ENV=production`.

The development OTP `246810` and OTP log sink are synthetic conveniences. Config
validation must prevent either value from being loaded outside `local` or `test`.

## Provider ports

Domain services depend on interfaces, not HTTP clients:

- `BumpaGateway`: analytics datasets, paginated orders and connection verification.
- `WhatsAppGateway`: template/text send and normalized delivery result.
- `AgentGateway`: tenant-profile chat and health/profile lifecycle operations.

The target is a deterministic fake and live adapter for each port. Current mocks
are in-process happy-path implementations and do not yet consume all versioned
fixtures or expose the documented failure scenarios (`timeout`, `rate_limited`,
`malformed`, `unavailable`).

## Bumpa contract

The defined read surface is ten analytics datasets plus paginated orders. Code and
documentation must not ambiguously call all eleven items “datasets.” Fixtures cover
`business_id` and `location_id`, Decimal money, unknown statuses, partial data,
body-level errors, 401/403/429/5xx, pagination and sensitive nested fields. Live
contract verification remains pending until a sandbox/test store is supplied.

## WhatsApp contract

Local webhook tests use canonical raw JSON bytes and compute real HMAC signatures.
They cover verification challenge, wrong/missing signature, unknown number, known
number, duplicates, delivery callbacks, STOP/START and out-of-order status events.
Outbound sends are recorded by the fake adapter. Live verification requires a Meta
app, verified callback, approved templates and a test recipient.

## Agent contract

The fake agent returns deterministic, tenant-tagged responses and captures only the
redacted context envelope. Contract tests assert that provider credentials and raw
PII never appear in the request. Live Hermes verification must prove profile process
topology, port allocation, authentication, restart behavior and cross-profile
isolation before the adapter is enabled.

## API contract

FastAPI OpenAPI is intended to be the source of truth. A checked-in OpenAPI
artifact, generated TypeScript client, drift comparison and MSW handlers have not
yet been added. Six Playwright checks exercise desktop/mobile public navigation,
the labelled demo chat path and privileged-host login reachability. The separate
`make integration` gate exercises the real FastAPI/Postgres path through the web
proxy without a browser.

## Synthetic seed

`make seed-demo` currently idempotently creates the backend's two synthetic tenants
and core roles/connections when the database is empty. UUIDs and timestamps are not
fixed, and the broader fixture matrix described by the build plan is not present.
`make reset-demo` now checks the running API environment, requires an explicit
confirmation, recreates the local Postgres schema through Alembic and reseeds it.
