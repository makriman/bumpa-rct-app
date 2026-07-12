# Development and Integration Contracts

## Current implementation boundary

This file describes the provider and end-to-end contracts. In-process commerce,
messaging, classifier and agent adapters cover the deterministic local path. The
production path has direct Meta, Bumpa, and authenticated Hermes adapters, durable
Redis-backed jobs, a transactional outbox, rate limits, and worker/scheduler health.
`make integration` exercises Postgres-backed OTP, commerce sync, chat, research
logging and PDF reports through the same-origin Next.js proxy. Production
user/settings/admin/research views use authenticated APIs; an explicit, labelled
demo-cookie path remains only for credential-free local UI testing. Generated
OpenAPI clients/MSW handlers remain outside the current implementation.

Production can use `disabled` selectors independently for WhatsApp, Bumpa, or the
agent while an external activation gate is incomplete. Provider-dependent routes
fail closed, report an honest unavailable state, and never fall back to local
adapters. The production worker and scheduler use the durable queue runtime.

## Local mode

Local development is credential free. Copy `.env.example` to `.env`, then run
`make bootstrap` and `make dev`. Production configuration rejects every mock
adapter. The settings validator checks required, file-backed Meta, Bumpa, and
Hermes configuration when the corresponding selector is used.

The development OTP `246810` and OTP log sink are synthetic conveniences. Config
validation must prevent either value from being loaded outside `local` or `test`.

## Provider ports

Domain services depend on interfaces, not HTTP clients:

- `BumpaGateway`: analytics datasets, paginated orders and connection verification.
- `WhatsAppGateway`: template/text send and normalized delivery result.
- `AgentGateway`: tenant-profile chat and health/profile lifecycle operations.

Each port has a deterministic fake and a production adapter. Contract tests cover
bounded timeout, retry, rate-limit, malformed/unavailable, idempotency, and ambiguous
send behavior at the adapter/job boundaries.

## Bumpa contract

The defined read surface is ten analytics datasets plus paginated orders. Code and
documentation must not ambiguously call all eleven items “datasets.” The direct
adapter enforces allowlisted endpoints, response/page limits, bounded retries,
Decimal money, unknown-value preservation, and deep redaction. All five supplied
business credentials have been authenticated; four completed every dataset and
orders canary, while one had a transient timeout on a single overview endpoint.
That provider observation is not production activation evidence.

## WhatsApp contract

Local webhook tests use canonical raw JSON bytes and compute real HMAC signatures.
They cover verification challenge, wrong/missing signature, unknown and known
senders, duplicates, durable acknowledgement, delivery callbacks, STOP/START, and
retry after job failure. The live adapter has a versioned sender, service-window
rules, idempotency, and an ambiguous-send guard. Meta account validation succeeded,
but phone verification, callback subscription, approved templates, and a live
delivery receipt remain external activation gates.

## Agent contract

The fake agent returns deterministic, tenant-tagged responses and captures only the
redacted context envelope. The Hermes runtime uses a pinned upstream-derived image,
an authenticated private gateway per profile, staged profile directories, and a
Hermes-only Anthropic secret. Contract tests cover authentication, lifecycle,
redacted context, and profile isolation; a production profile and Claude canary are
still required before claiming the production path live.

Claude is the model provider through Hermes. The Anthropic key belongs only to the
Hermes runtime secret boundary; FastAPI passes tenant-scoped,
redacted context to Hermes and does not call Claude directly for the SME chat path.
No Hermes database row, profile directory, port allocation or credential alone is
live-profile evidence.

## API contract

FastAPI OpenAPI is intended to be the source of truth. A checked-in OpenAPI
artifact, generated TypeScript client, drift comparison and MSW handlers have not
yet been added. Eight Playwright project checks exercise desktop/mobile public navigation,
the labelled demo chat path and privileged-host login reachability. The separate
`make integration` gate exercises the real FastAPI/Postgres path through the web
proxy without a browser.

## Synthetic seed

`make seed-demo` currently idempotently creates the backend's two synthetic tenants
and core roles/connections when the database is empty. UUIDs and timestamps are not
fixed, and the broader fixture matrix described by the build plan is not present.
`make reset-demo` now checks the running API environment, requires an explicit
confirmation, recreates the local Postgres schema through Alembic and reseeds it.
