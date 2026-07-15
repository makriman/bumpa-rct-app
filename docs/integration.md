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
Decimal money, unknown-value preservation, and deep redaction. Every connection
persists an explicit IANA `store_timezone` and three-letter `store_currency`.
For a top-level analytics request whose query bounds are store-local dates, the
response `range` must encode the exact UTC instants corresponding to the inclusive
start and end of those local days. Canonical schema-v1 keeps the local dates while
metric evidence retains the exact instants. Time-bearing values require an explicit
offset; nested current/previous periods may use exact instants or date-only values
only when their derived local dates match the validated parent or immediately
adjacent comparison range. Previous-period length may differ by at most three
calendar days. Analytics currency evidence must be valid and consistent with
`store_currency`. Each order may instead carry its own valid three-letter currency,
with `store_currency` used only as a fallback; conflicting aliases, nested line-item
claims, malformed codes, or invalid monetary facts fail the page closed. Customer
ranking identity is removed, anonymous/deleted-customer aggregate counts remain
usable, and product ranking labels accept product-specific fields only.

Every queued sync captures the active `boundary_revision`. A material provider,
scope, timezone, or currency replacement invalidates queued work, fences in-flight
publication, clears mutable canonical orders, and retains older evidence for audit
without exposing it to current product reads. A verified same-boundary key rotation
preserves the revision and current projections.

The bounded pre-release five-key adapter probe returned eight available analytics
datasets and the two typed profit limitations for stores 1–4. Store 5 returned seven
available datasets, the two profit limitations, and one isolated upstream
`products.overview` failure. Orders decoded for all five stores, including valid
historical per-order currencies. This proves adapter decoding and typed failure
handling only; schema-0015 durable production canaries and redacted persisted-data
reconciliation remain release gates.

## WhatsApp contract

Local webhook tests use canonical raw JSON bytes and compute real HMAC signatures.
They cover verification challenge, wrong/missing signature, unknown and known
senders, duplicates, durable acknowledgement, delivery callbacks, STOP/START, and
retry after job failure. The live adapter has a versioned sender, service-window
rules, idempotency, and an ambiguous-send guard. The production phone is verified,
Cloud API is connected, callback subscriptions are active, and signed public ingress
passes. Meta Business verification, approved OTP/insight templates, and a live
outbound delivery receipt remain external activation gates.

### Meta test-sender lane

The optional Meta test sender is a separate, deliberately limited ingress/reply
lane. It is configured with all three of the following identifiers, which are not
interchangeable:

- `META_TEST_SENDER_WABA_ID`: the test WhatsApp Business Account ID.
- `META_TEST_SENDER_PHONE_NUMBER_ID`: the numeric Graph API phone-number ID.
- `META_TEST_SENDER_DISPLAY_PHONE_E164`: the normalized display number.

Set `META_TEST_SENDER_VERIFICATION_MODE=inbound_replies_only` only after all three
values are known and the app is subscribed to that WABA. Webhook events must match
both the entry WABA ID and `metadata.phone_number_id`; mismatches are durably ignored.
Replies to an accepted test-lane event use that event's sender, while proactive
messages and OTPs continue to use the primary production sender. The test lane has
`supports_otp=false`: it must never initiate an authentication code, claim that a
code was sent, or substitute for an approved production authentication template.
Keep the mode `disabled` when any identifier or subscription is uncertain.

Read-only Graph checks confirm that the current test WABA and phone-number ID pair
with the configured display number, and its app subscription list is non-empty.
The sender reports `PENDING` and has five approved non-authentication templates but
zero authentication templates. Both attempted authentication-template create
endpoints were denied with Graph code `10` and subcode `2388185`, so this is an
external account/permission gate rather than application delivery evidence. No
outbound message was sent. The lane remains reply-only with `supports_otp=false`
and cannot satisfy OTP, proactive insight, authentication-template or delivery-
receipt gates.

## Agent contract

The fake agent returns deterministic, tenant-tagged responses and captures only the
redacted context envelope. The Hermes runtime uses a pinned upstream-derived image,
an authenticated private gateway per profile, staged profile directories, and a
Hermes-only Anthropic secret. Contract tests cover authentication, lifecycle,
redacted context, and profile isolation. All five production profiles pass
authenticated health and each completed an explicitly authorized live Claude
request through its own Hermes gateway. Forty foreign-profile gateway/control
attempts were rejected, and an audited restart plus post-restart completion passed.
WhatsApp routing and unrestricted launch readiness remain separate gates.

Claude is the model provider through Hermes. The Anthropic key belongs only to the
Hermes runtime secret boundary; FastAPI passes tenant-scoped,
redacted context to Hermes and does not call Claude directly for the SME chat path.
No Hermes database row, profile directory, port allocation or credential alone is
live-profile evidence.

## API contract

FastAPI OpenAPI is the source of truth. The repository contains a redacted,
deterministic `contracts/openapi.json` artifact and generated
`apps/web/lib/generated/api-contract.ts`; CI rejects drift in the backend artifact
and generated client. MSW handlers are not implemented. Twenty-six Playwright
project checks exercise desktop/mobile public navigation, OTP-to-chat, fail-closed
role boundaries, resumable operator onboarding, team mutation, Bumpa evidence,
research filtering/report queueing, responsive navigation, accessibility, visual
baselines and production nonce CSP. The exact-release web gate also passes 128
unit/component tests across 23 files and a production build.
The separate `make integration` gate exercises the real FastAPI/Postgres path
through the web proxy.

## Sealed resilience contract

`make load-failure` creates a disposable Compose project with synthetic-only
credentials, explicit Postgres/Redis hosts, mock provider selectors and fresh
volumes. The runner disables ambient Compose env files, rejects inherited live
data-plane/provider values, validates the rendered and running environments, and
removes the project after the run. It produces one JSON artifact covering exact
authenticated chat/sync rate-limit outcomes, idempotent chat replay, tenant-safe
negative reads and PostgreSQL invariants; a signed 50-event webhook burst and
replay; Redis/Postgres outage recovery; and a deterministic near-full disk event
through the production sanitizer/HMAC boundary. The disk drill intercepts only
the final transport and performs no external network request.

## Synthetic seed

`make seed-demo` currently idempotently creates the backend's two synthetic tenants
and core roles/connections when the database is empty. UUIDs and timestamps are not
fixed, and the broader fixture matrix described by the build plan is not present.
`make reset-demo` now checks the running API environment, requires an explicit
confirmation, recreates the local Postgres schema through Alembic and reseeds it.
