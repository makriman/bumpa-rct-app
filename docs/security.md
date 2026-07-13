# Security and Privacy

## Implementation status

The sections below are the required security baseline. They are not a statement
that every control is already present. Implemented local controls include signed
WhatsApp webhook verification, hashed/expiring/single-use OTP records, revocable
JWT sessions, application RBAC checks, authenticated field encryption, deep
non-mutating structured/text redaction, explicit Postgres RLS policy DDL, and
non-root application images. A local non-owner/non-bypass Postgres probe,
production-off demo build default and local image vulnerability scans have also been
verified. Cookie-origin CSRF enforcement, Redis-backed phone/IP rate limits,
raw-access reason gates, privacy-bounded audit request context, draining operational
retention, export expiry/cleanup and dry-run-first credential key rotation are
implemented and tested. The first dual-reader release deliberately retains v1
writes for rollback; production v2 rewrapping remains a staged operational gate.
Exact-registry scan evidence for all six deployed hardened images is complete.
Consult the verification ledger before relying on a control.

Production may use explicit `disabled` provider modes while an external account gate
is incomplete; worker and scheduler still run the durable internal runtime. A
disabled selector is a containment state, not a security signoff for provider
integrations. A local mock response in production, an enabled provider
without its contract/canary gate, or a running production idle-shell worker is a
release-blocking configuration defect.

## Assets and threats

High-value assets are provider credentials, customer/order PII, raw chat, tenant
business metrics, research datasets, session/OTP material and backups. Principal
threats are cross-tenant access, broken role checks, webhook forgery/replay, prompt
injection into tools, credential leakage, export exfiltration, queue replay, SSRF
through connectors and compromised dependencies/images.

## Required controls

### Authentication and sessions

- OTPs are randomly generated, hashed with a dedicated secret, short-lived,
  single-use and protected by phone/IP attempt limits. Claude chat, WhatsApp chat,
  Bumpa sync, and research report generation have separate tenant/user/phone budgets.
- The deterministic OTP and log sink exist only when `APP_ENV` is `local` or `test`.
- Browser sessions use `Secure`, `HttpOnly`, appropriate `SameSite` cookies, CSRF
  protection on mutations, rotation after authentication and server-side revocation.
- Authentication responses avoid account enumeration and all login events are
  correlated without logging the raw phone number or OTP.

### Tenant and role isolation

- Application authorization and Postgres RLS both enforce tenant scope.
- Tests use a non-owner database role and attempt ID, query, host and filter tampering.
- Researcher data is pseudonymized/redacted by default. Raw access is time-bound,
  reason-gated and audit logged.
- Admin mutation and audit record commit atomically.

### Provider and agent safety

- Meta webhook bodies are verified against `X-Hub-Signature-256` before parsing.
- Meta message IDs and job idempotency keys prevent duplicate processing.
- External clients have bounded timeouts, response-size limits, retry policy and
  allowlisted base URLs. MCP never accepts arbitrary production URLs.
- Tool execution reconstructs tenant scope server-side; model-provided tenant IDs,
  URLs, credentials and authorization claims are ignored.
- MCP providers, OAuth endpoints, scopes and tools come from a fixed server-side
  registry. Connections require a tenant-admin request plus platform-operator
  approval before OAuth can start. OAuth state is authenticated, encrypted,
  short-lived and identity-bound; returned tokens are response-bounded and
  encrypted at rest.
- MCP read tools are allowlisted individually. Write tools require an approved
  non-read-only connection, an explicit permission record and fresh user
  confirmation for every invocation. Revocation immediately deletes local access
  and attempts a fixed-origin upstream token revocation without blocking local
  safety when the provider is unavailable.
- Secrets and raw PII never enter prompts or tool telemetry.

### Secrets

`.env.production` exists only on the host with mode `0600`. Repository and CI scans
cover full history. Tenant credentials are encrypted with a versioned envelope so
key rotation can decrypt with the old key and re-encrypt with the new key. Logs must
redact authorization headers, cookie values, phone numbers, addresses and raw
provider payloads.

The cipher authenticates v2 key IDs as associated data, fails closed on unknown or
malformed envelopes, bounds the old-key ring, and preserves legacy v1 reads. The
rotation command authenticates every durable credential before mutation, locks the
selected rows, rolls back on any failure, defaults to a sanitized dry run and
requires explicit confirmation to apply. Production follows the two-phase
rollback-safe sequence in `docs/runbook.md`; no live v2 rewrite or key-material
rotation is claimed until its backup, dry-run/apply, OAuth TTL grace and post-run
evidence exist.
The Anthropic key is owned by the Hermes runtime boundary only. It must not
be passed through the shared Compose application environment or stored per tenant.

### Containers and network

The rendered Compose contract publishes only Caddy ports and keeps database/cache
networks internal. Application Dockerfiles use non-root users; the hardened Caddy
runtime uses fixed UID/GID `10001`, a read-only root filesystem and only
`NET_BIND_SERVICE`. Backup runs as UID/GID 70 with a narrow capability set; the
separate destructive restore profile adds `DAC_OVERRIDE` and is never a standing
service. Production enables `no-new-privileges`, and exact image references are
required. The release workflow publishes commit-SHA-tagged images with provenance
and SBOM, then scans each exact registry digest. Delivery-hardening
[PR 41](https://github.com/makriman/bumpa-rct-app/pull/41) and its CI run
29290441375 passed 13/13 jobs. Protected-main CI 29290795169 passed 13/13 jobs and
publish run 29291129708 passed 7/7 jobs for all six images deployed at release
`b35762ab2a9d5c1a4956530cae63040354805510`. Redis remains pinned to its reviewed
upstream digest.

## Research governance

The governance contract gates research collection by recorded consent, retains
consent version/timestamp/policy version, stops new classification after withdrawal,
and initiates retention/deletion without silently corrupting operational records.
The current flow gates research events and reads, stores consent history,
invalidates artifacts after withdrawal, expires generated artifacts after 24 hours,
reauthorizes every download, and enqueues production retention cleanup. Audit logs
default to 365 days and sanitized system errors to 90 days; bounded continuations
drain an expired backlog with locked/skipped rows. The reviewable policy draft is
`docs/privacy-retention-policy.md`; named privacy/security approval and the open
durable product-data windows remain release governance steps.

## Verification

Security is evidenced by executable negative tests, not configuration screenshots.
Critical branches—authorization, RLS, signature verification, dedupe, encryption,
redaction and export permissions—target complete branch coverage. See
`docs/verification.md` for claim status and evidence locations.

The current production audit confirms ENABLE+FORCE RLS and one policy on all 23
tenant tables. Its non-bypass application role exercised 115 tenant/table contexts
across 670 scoped rows and returned zero rows without tenant context and zero
cross-tenant rows. All eight services are running, all seven configured
healthchecks are healthy, and every service has zero restarts and zero OOM kills.

All five branded records are Cloudflare-proxied with Full (strict), Always Use
HTTPS, minimum TLS 1.2 and TLS 1.3 enabled. External probes reject TLS 1.0/1.1 on
every host. The edge strips spoofed nonce/CSP inputs; request-rendered documents use
unique nonce-bearing CSP without script `unsafe-inline` or `unsafe-eval`, suppress
the internal nonce header, and are marked `private, no-store`.

Live-provider evidence does not broaden authorization. Five Hermes profiles have
completed one live Claude request each. Forty cross-profile gateway/lifecycle
attempts were rejected, an audited demo-profile restart recovered, a post-restart
Claude completion succeeded, and all profiles returned healthy with no Hermes
system errors; WhatsApp channel routing remains unproven. Bumpa is partial at 8/10
analytics datasets for stores 1–4 and 7/10 for degraded store 5;
`products.overview` timed out/returned HTTP 504. Redacted raw/metric/canonical
counts reconcile for all five runs, but the provider still does not supply 10/10.
The subscribed Meta test lane reports `PENDING`, has five approved
non-authentication templates but zero authentication templates, remains reply-only
with `supports_otp=false`, and sent no outbound message after both auth-template
create paths were denied with Graph code `10`/subcode `2388185`. The Meta sender is
not OTP- or launch-ready. `OFFSITE_BACKUP_SCRIPT` is unset, external alert delivery
is absent, and formal privacy/retention approval remains an open security and
governance gate.
