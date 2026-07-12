# Architecture

## Status and reading guide

This document describes the implemented architecture. FastAPI, Next.js, Postgres,
Redis, the durable worker/scheduler runtime, and private Hermes profile gateways are
wired through explicit service boundaries. The application has deterministic local
adapters and separately configured production adapters for Meta, Bumpa, and Hermes.
Explicit RLS was exercised through the non-owner `bumpabestie_app` role on local
Postgres. Labelled demo fixtures remain available only for credential-free local UI
testing. Production rejects mock providers, mounts secrets only into the services
that consume them, and fails unavailable rather than falling back to a local
adapter. See `docs/build-plan-compliance.md` and `docs/verification.md` for
environment-specific evidence status.

## System boundary

Bumpa Bestie is a multi-tenant control plane, not a direct browser-to-agent
integration. Every request follows the same authorization chain:

```text
authenticated web user or verified WhatsApp sender
  -> user identity
  -> active tenant membership
  -> tenant-scoped business context
  -> selected agent profile
  -> response and tool-call records
  -> redacted research event and audit evidence
```

FastAPI owns identity, RBAC, tenant context, provider credentials, approved tool
calls and research permissions. Agent runtimes receive compact context only. The
Next.js server and browser never receive Bumpa, Meta, Anthropic or Hermes secrets.

## Target components

- **Caddy** is the sole published ingress and routes by validated host.
- **Next.js** provides public, SME, admin and research surfaces from one codebase;
  authenticated production views call FastAPI through the same-origin API proxy.
- **FastAPI** is the synchronous control plane and owns the OpenAPI contract.
- **Worker** executes outbound messages, provider sync, classification and exports.
- **Scheduler** enqueues idempotent scheduled work; it does not execute jobs inline.
- **Postgres** is the source of truth and enforces RLS as defense in depth.
- **Redis** provides queues, bounded rate-limit state and short-lived coordination.
- **Provider adapters** implement Bumpa, WhatsApp and agent ports. Deterministic
  mocks are first-class local/test adapters; Meta, direct Bumpa and Hermes adapters
  are separately configured and fail closed.

The API, worker and Hermes service attach to a non-published `egress` network
because they must reach provider APIs. Postgres and Redis attach only to the
`internal: true` `data` network. Caddy reaches web and API through the private `app`
network and is the only service with host ports.

## Tenancy and authorization

Tenant membership roles are `owner`, `admin` and `member`. Platform roles such as
`operator`, `researcher` and `superadmin` are global capabilities and must not be
modeled as membership in an arbitrary SME. Route dependencies perform authorization
before repository queries, then set `app.current_tenant_id` transaction-locally.

RLS checks run through a database role without owner or `BYPASSRLS`. Privileged
cross-tenant operations use a distinct dependency and database path, require a
reason where raw data is involved, and write an audit record in the same transaction
as the mutation.

Host routing is presentation, not authorization. Middleware must reject admin and
research paths on the public host and reject public-route confusion on privileged
hosts. FastAPI independently verifies roles for every privileged API.

## Required data and job invariants

- Tenant-owned rows include `tenant_id`; parent/child foreign keys cannot cross
  tenants.
- Monetary values use `NUMERIC`/`Decimal`; serialization preserves decimal text.
- Upstream payloads are retained only under explicit access controls and retention.
- Unknown enum values are stored safely; upstream errors become unavailable/error,
  never fabricated zeroes.
- Webhook and job idempotency keys are unique and claimed transactionally.
- Transactional outbox records connect state changes to queued side effects.
- Retries are bounded with exponential backoff and terminal failures are visible.
- Timestamps are UTC; tenant timezone is applied only at presentation boundaries.

## Runtime profiles

Local and CI use mock provider adapters and synthetic seed data. Production rejects
mock adapters and may use an explicit `disabled` selector while only infrastructure
is being verified. A disabled provider is not a healthy/live provider and makes the
dependent product action unavailable. Live provider configuration and the Hermes
process/profile topology must pass their contract and canary checks before
activation; a port range, database row or mounted profile directory alone is not
proof of profile isolation.

The production service set includes Caddy, web, API, worker, scheduler, Hermes,
Postgres, and Redis. Worker and scheduler claim durable jobs through Redis-backed
queues; Hermes exposes authenticated, private per-profile gateways. Provider
selectors can remain disabled independently while their external activation gates
are incomplete, preserving fail-closed behavior without misreporting readiness.

## Portability

Compose files contain no host-specific absolute paths. Images target `linux/amd64`;
production consumes exact immutable digests, drops Linux capabilities where the
service permits and uses read-only filesystems where the application permits.
Persistent state is confined to named volumes and export/backup interfaces. The same
images may later run on another container platform without changing domain logic or
provider ports.
