# Security and Privacy

## Implementation status

The sections below are the required security baseline. They are not a statement
that every control is already present. Implemented local controls include signed
WhatsApp webhook verification, hashed/expiring/single-use OTP records, revocable
JWT sessions, application RBAC checks, authenticated field encryption, basic text
and order redaction, explicit Postgres RLS policy DDL, and non-root application
images. A local non-owner/non-bypass Postgres probe and production-off demo build
default have also been verified. Still pending are CSRF protection, IP/Redis rate
limits, credential key rotation, raw-access reason gates, export expiry, nested PII
redaction coverage and image vulnerability scanning. Consult the verification
ledger before relying on a control.

## Assets and threats

High-value assets are provider credentials, customer/order PII, raw chat, tenant
business metrics, research datasets, session/OTP material and backups. Principal
threats are cross-tenant access, broken role checks, webhook forgery/replay, prompt
injection into tools, credential leakage, export exfiltration, queue replay, SSRF
through connectors and compromised dependencies/images.

## Required controls

### Authentication and sessions

- OTPs are randomly generated, hashed with a dedicated secret, short-lived,
  single-use and protected by phone/IP attempt limits.
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

### Containers and network

The rendered Compose contract publishes only Caddy ports and keeps database/cache
networks internal. Application Dockerfiles use non-root users; the production
overlay drops capabilities and enables `no-new-privileges`. The release workflow
is configured to publish commit-SHA-tagged images with provenance and SBOM. No
image has yet been published or vulnerability-scanned as evidence.

## Research governance

Research collection is gated by recorded consent status. Consent version, timestamp
and policy version are retained. Withdrawal stops new research classification and
initiates the documented retention/deletion workflow without silently corrupting
operational records. Raw chat, raw commerce payloads and anonymized research data
have separate retention schedules. Export files expire and require reauthorization
on download.

## Verification

Security is evidenced by executable negative tests, not configuration screenshots.
Critical branches—authorization, RLS, signature verification, dedupe, encryption,
redaction and export permissions—target complete branch coverage. See
`docs/verification.md` for claim status and evidence locations.
