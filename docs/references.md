# External references

These are the primary project references named by the build plan. A link is useful
design input; it is not evidence that the corresponding integration works. Record
the exact API/version used, a redacted contract fixture, and a live canary when an
adapter is implemented.

## Hermes Agent

- [Profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles/) —
  intended source for per-tenant profile layout and lifecycle.
- [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker/) — intended
  source for the pinned runtime image and private container topology.
- [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/) —
  intended source for API authentication, request shape and health behavior.

The runtime image is derived from the immutable Hermes image index pinned in
`infra/hermes/Dockerfile`. CI exercises its secret-file entrypoint, networkless
profile importer, private runtime, profile permissions and backup/restore contract.
Per-tenant functional and isolation canaries are still required after every live
profile activation; an image-level contract is not a substitute for those canaries.

## Bumpa

- [Bumpa documentation](https://docs.bumpa.io/) — public documentation entry point.
- [Bumpa product/API landing page](https://www.bumpa.io/) — product context and the
  route to provider support/access.

The repository uses a direct server-side Bumpa REST adapter; Bumpa MCP is out of
scope. The adapter has authenticated all five supplied business credentials. Four
completed the bounded live provider probe; store 5 returned a bounded degraded
timeout with usable prior data. Public landing material and credential probes do
not prove a mapped production sync/reconciliation contract, so retain versioned
redacted fixtures and re-run tenant-scoped canaries before each activation.

## Meta WhatsApp Cloud API

- [Message API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/message-api/) — outbound message contract.
- [Incoming webhook payload](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/webhooks/whatsapp-incoming-webhook-payload/) — inbound and delivery event contract.
- [Template API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-account/template-api/) — template creation, approval and status.

Pin and test the selected Graph API version. Do not infer current template, service
window, token or permission rules from the build-plan date.

## Approved MCP OAuth providers

- [Google OAuth for web-server applications](https://developers.google.com/identity/protocols/oauth2/web-server) — authorization-code exchange, redirect URI and offline-access contract.
- [Google OAuth security practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices) — encrypted token storage, revocation and provider-side safeguards.
- [Meta Login manual flow](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow/) — authorization-code flow used by the fixed Meta Ads registry entry.

MCP connectors remain disabled until their provider OAuth client, exact callback
URI and secret-file mount are configured. The application never accepts a
user-supplied authorization URL, token URL, scope or MCP server address.

## Anthropic Claude

- [Messages API](https://platform.claude.com/docs/en/api/messages) — model request and response contract.
- [API versioning](https://platform.claude.com/docs/en/api/versioning) — required
  version behavior.

Claude is reached through Hermes for the SME agent path. The Anthropic key belongs
only in the Hermes secret boundary; it must not be injected into web, API, worker,
scheduler, browser or research exports. Model names must be selected from the
current Anthropic console immediately before integration.

## DigitalOcean

- [Recommended Droplet setup](https://docs.digitalocean.com/products/droplets/getting-started/recommended-droplet-setup/) — host account, SSH and baseline hardening.

DigitalOcean console access and an authorized SSH public key are external
prerequisites. Never put a Droplet password or SSH private key in this repository.

## Docker and application deployment

- [Docker Compose in production](https://docs.docker.com/compose/how-tos/production/) — production overrides and operations.
- [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) —
  reference for a future secret-file/manager migration.
- [FastAPI in containers](https://fastapi.tiangolo.com/deployment/docker/) — API image and process guidance.
- [FastAPI behind a proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) — forwarded-header and trusted-proxy guidance.
- [Next.js deployment](https://nextjs.org/docs/pages/getting-started/deploying) — standalone server deployment guidance.

## PostgreSQL

- [Row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) —
  authoritative RLS semantics, including owner and `BYPASSRLS` behavior.

RLS evidence must run through the non-owner, non-`BYPASSRLS` application role;
running the same query as a table owner is not a valid isolation test.

## Architecture decision: deterministic ReportLab PDF rendering

**Status:** approved on 2026-07-13 by the integration owner.

The build plan proposed HTML templates rendered to PDF by Playwright. The approved
implementation instead uses ReportLab in the Python report worker as the single PDF
renderer. This keeps asynchronous report generation portable across local Compose,
CI and the Droplet; avoids installing a browser and its sandbox/runtime attack
surface in the worker; and produces deterministic artifacts that are parsed in
tests with `pypdf`. The renderer provides multi-page A4 output, bounded tables,
charts, disclosure labels, page numbering and a record appendix. Checksums, expiry,
raw-access authorization and consent invalidation remain independent of the visual
renderer.

This substitution is deliberate, not an unimplemented fallback. The acceptance
contract is deterministic parser tests for required sections and metadata, privacy
and integrity tests for every artifact format, and a production visual review after
the candidate image is deployed. The optional DOCX format remains out of scope.

## Local development boundary

Codex usage is governed by `CODEX.md` and `bumpabestie-buildplan.md`. Codex is a
local development aid only and is not installed, credentialed or run on the
DigitalOcean host.
