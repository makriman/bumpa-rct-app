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

### Temporary web-only authentication

The live contained release uses a deliberately temporary shared six-digit pilot
PIN while WhatsApp authentication is parked. It is production-evidenced only for
the five approved mapped collaborators and is not a long-term identity factor.
Only an active user whose primary phone has an approved,
non-opted-out mapping to an active tenant membership can receive a provider-free
challenge; platform role alone is insufficient. Request/verification responses are
generic, challenges are short-lived and single-use, attempt and Redis phone/IP
limits apply, and audit resource IDs use a one-way keyed phone reference.

The server stores only an `OTP_SECRET`-peppered HMAC verifier in a dedicated
root-owned `0700` host directory as a root-owned `0600` file. An exact-digest,
networkless, read-only API image with every capability dropped validates it
without emitting it; a separate networkless initializer creates an API-only
`0400` runtime copy. The Docker-enabled deployment account is explicitly trusted
as a root-equivalent operator, so host ownership protects against application
services and accidental reads—not that privileged principal. Raw PIN material is
never written, mounted into other services or displayed by the product. A
mandatory future expiry and `AUTH_LOGIN_MODE=disabled` are independent kill
switches. The residual threat is explicit: knowledge of both an
approved phone and the shared PIN is sufficient to impersonate that mapped user,
so the mode is appropriate only for the bounded pilot and must be rotated or
removed when WhatsApp identity proof is ready.

Authorization is unchanged. Tenant membership, `operator`, `researcher` and the
protected `superadmin` role remain independent. A superadmin can auditably grant or
revoke only operator/researcher access through the ordinary lifecycle. In temporary
PIN mode, grants are limited to collaborators whose primary phone is already mapped
to an active membership and tenant; legacy grant calls cannot create an unmapped
platform identity. Cookies stay host-only, `Secure`, `HttpOnly` and `SameSite=Lax`;
host-aware navigation never elevates a user or widens a cookie to sibling
subdomains.

At the public ingress, strict Cloudflare trusted-proxy ranges allow Caddy to replace
the private client-IP header from `CF-Connecting-IP`; direct or untrusted peers use
their socket address. The Next.js proxy accepts one validated IP, discards browser
forwarding chains and passes it to FastAPI only for privacy-preserving limits. See
[`docs/temporary-web-login.md`](temporary-web-login.md) for the complete operating
and verification contract.

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
and SBOM, then scans each exact registry digest. Brand/data hardening
[PR 49](https://github.com/makriman/bumpa-rct-app/pull/49), persistence
[PR 51](https://github.com/makriman/bumpa-rct-app/pull/51), the final timeout
[PR 52](https://github.com/makriman/bumpa-rct-app/pull/52), protected-main
[CI 29412671738](https://github.com/makriman/bumpa-rct-app/actions/runs/29412671738)
and [publish run 29413085773](https://github.com/makriman/bumpa-rct-app/actions/runs/29413085773)
passed for all six images deployed at application release
`c0c15443352ab84fde1d2edfde1ed0692ed842f6`. Redis remains pinned to its reviewed
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

Historical predecessor production evidence confirms ENABLE+FORCE RLS and one
policy on all 23 tenant tables. Its non-bypass application role exercised 115
tenant/table contexts across 670 scoped rows and returned zero rows without tenant
context and zero cross-tenant rows. Current exact-release CI reran the RLS
contracts; schema-0015 store-boundary reconciliation adds current production
evidence without relabelling that historical full-catalog audit. All eight services
are running, all seven configured healthchecks are healthy, and accepted runtime
samples show zero restarts and zero OOM kills.

All five branded records are Cloudflare-proxied with Full (strict), Always Use
HTTPS, minimum TLS 1.2 and TLS 1.3 enabled. External probes reject TLS 1.0/1.1 on
every host. The edge strips spoofed nonce/CSP inputs; request-rendered documents use
unique nonce-bearing CSP without script `unsafe-inline` or `unsafe-eval`, suppress
the internal nonce header, and are marked `private, no-store`. Five public routes
carry route-specific canonical/OG/Twitter metadata; the homepage additionally
carries nonce-bearing JSON-LD. Private login,
workspace, admin and research surfaces enforce `X-Robots-Tag: noindex` even though
their shared document shell retains the public default meta tag.

Live-provider evidence does not broaden authorization. Five Hermes profiles each
completed a current live Claude request from a synthetic prompt; normal
tenant-scoped redacted context remained inside Hermes and prompt/response bodies
were omitted from evidence. Five same-profile health checks passed, and all 20
cross-profile gateway credential attempts were rejected; cleanup left zero active
canary sessions and no new Hermes errors. WhatsApp channel routing remains
unproven. Bumpa is accepted partial at 8/10 analytics datasets plus orders for
stores 1–4 and degraded at 7/10 plus orders for store 5. Store 3's slow
`products.overview` succeeds under the scoped 90-second policy; the same dataset
alone receives no provider response for store 5 inside that boundary. Current raw
evidence, 50 metric snapshots, canonical orders/items, ranges, currency, redaction
and freshness reconcile, but the provider still does not supply 10/10.
Historical predecessor Graph evidence reported the Meta test lane `PENDING`, with
five approved non-authentication templates, zero authentication templates and both
auth-template create paths denied. It was not rerun for this application release
and is not current sender evidence. WhatsApp is disabled, and no outbound message,
OTP or receipt is claimed. The Meta sender is not OTP- or launch-ready.
`OFFSITE_BACKUP_SCRIPT` is unset, external alert delivery is absent, and formal
privacy/retention approval remains an open security and governance gate.
