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
raw-access reason gates, audit logging, and export expiry/cleanup are implemented
and tested. Credential key-version rotation remains pending. Exact-registry scan evidence
for all five deployed hardened images is complete. Consult the verification ledger
before relying on a control.

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
- Secrets and raw PII never enter prompts or tool telemetry.

### Secrets

`.env.production` exists only on the host with mode `0600`. Repository and CI scans
cover full history. Tenant credentials are encrypted with a versioned envelope so
key rotation can decrypt with the old key and re-encrypt with the new key. Logs must
redact authorization headers, cookie values, phone numbers, addresses and raw
provider payloads.

The versioned rotation workflow in the previous paragraph is a required target; the
current field cipher does not yet supply complete key-version migration evidence.
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
and SBOM, then scans each exact registry digest. Publish run 29205487124 completed
that gate for all six application/infrastructure images deployed at release
`41935d67696fee45b184a65c0a9bf39e0708ae89`; Redis remains pinned to its
reviewed upstream digest.

## Research governance

The governance contract gates research collection by recorded consent, retains
consent version/timestamp/policy version, stops new classification after withdrawal,
and initiates retention/deletion without silently corrupting operational records.
The current flow gates research events and reads, stores consent history,
invalidates artifacts after withdrawal, expires generated artifacts after 24 hours,
reauthorizes every download, and enqueues production retention cleanup. Formal
retention-policy and privacy signoff remain release governance steps.

## Verification

Security is evidenced by executable negative tests, not configuration screenshots.
Critical branches—authorization, RLS, signature verification, dedupe, encryption,
redaction and export permissions—target complete branch coverage. See
`docs/verification.md` for claim status and evidence locations.
