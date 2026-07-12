# Build-plan compliance and evidence ledger

## Purpose

This ledger maps `bumpabestie-buildplan.md` to the repository as it exists. It is
an implementation audit, not a replacement specification and not a launch
certificate. A source file proves that code exists; only a passing test or an
environment-specific transcript proves that it works in that environment.

The plan's one-shot definition of done explicitly requires the complete product on
the DigitalOcean Droplet. That definition is **not yet met**. The repository has a
tested local mock implementation and is being hardened for a provider-disabled
production infrastructure baseline. That baseline must not receive SME or research
traffic.

## Status vocabulary

| Status | Meaning |
|---|---|
| `implemented-tested` | The repository contains the capability and identified automated or local integration evidence has passed. This does not imply live-provider or production proof. |
| `production-baseline-disabled` | The capability intentionally fails closed or its service is not started in production because its production dependency is incomplete. This is a safe state, not feature completion. |
| `deferred-provider` | The live Meta, Bumpa, Hermes/Claude, or related provider integration is intentionally deferred and has no live canary evidence. |
| `external-blocked` | Completion depends on infrastructure or credentials outside the repository, such as DNS, SSH authorization, provider accounts, or off-host backup storage. |
| `partial` | A meaningful subset exists, but one or more build-plan requirements or acceptance tests are missing. |
| `not-implemented` | No working implementation was found for the stated requirement. |

Statuses are deliberately not cumulative. For example, signed webhook handling can
be `implemented-tested` in local mock mode while outbound Meta delivery remains
`deferred-provider` and the production webhook remains
`production-baseline-disabled`.

## Evidence hierarchy

1. `production`: a revision, immutable image digest, timestamp, production command,
   and redacted result.
2. `live`: a provider sandbox/canary transcript tied to a revision.
3. `local`: a reproducible Compose or integration transcript tied to a revision.
4. `contract`: deterministic automated tests and fixtures.
5. `source-only`: implementation presence without execution evidence.

Evidence cannot be promoted across these boundaries. In particular, local mock
tests cannot close live-provider rows, and an HTTP 200 readiness response does not
prove provider reachability unless the readiness implementation actually probes the
provider.

## Release boundary at this revision

| Boundary | Status | What is true now | What remains before real use |
|---|---|---|---|
| Local developer product path | `implemented-tested` | Compose, migrations, synthetic OTP, local commerce sync, browser chat, API-backed user/settings/admin/research views, signed WhatsApp fixtures, research events, and CSV/JSONL/PDF artifacts have local evidence. | Expand provider/load/failure coverage and accessibility/visual QA. |
| Production infrastructure baseline | `production-baseline-disabled` | Production configuration rejects mock adapters. The hardened Ubuntu host and non-root deploy account are ready; the intended baseline starts Caddy, web, API, Postgres, and Redis, while workers/scheduler and provider-dependent actions remain unavailable. | Bind gates to the release SHA, publish immutable images, obtain temporary-host TLS, deploy, and record smoke/backup evidence. |
| WhatsApp/Meta | `deferred-provider` | HMAC verification, dedupe, routing, STOP/START, delivery-state handling, and retry behavior have deterministic local tests. | Live sender, OTP/templates, verified callback, durable queue consumer, rate limits, live delivery receipts, and failure canaries. |
| Hermes/Claude | `deferred-provider` | A deterministic local agent port and database profile record exercise tenant routing concepts. Claude credentials are not shared with API/worker/scheduler. | Hermes image/service, profile filesystem and process topology, Hermes-only Anthropic secret injection, authenticated client, profile lifecycle, private port isolation, and cross-profile live canary. |
| Bumpa | `deferred-provider` | Local commerce abstractions exercise Decimal money, availability, redaction, sync records, and canonical rows. | Direct live client, all ten analytics datasets plus paginated orders against a sandbox, versioned failure fixtures, durable jobs, limits, and reconciliation. |
| Off-host durability | `external-blocked` | Local backup, checksum, restore script, Compose backup volume, and a systemd timer definition exist. | Select and credential an encrypted off-host target, wire the exact latest-backup handoff, alert on both stages, and restore from that target. |
| Droplet and domains | `partial` | The key is authorized for root and the non-root deploy user; Ubuntu 24.04, Docker, UFW, fail2ban, unattended upgrades and swap are verified. Five temporary sslip.io hosts resolve to the Droplet. | Publish/pull images, obtain TLS and run production verification. The planned `bumpabestie.com` remains unregistered and is not launch-ready. |

## Section-by-section build-plan audit

### Sections 1–8: product, architecture, surfaces, workflow

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 1–21, project identity/target | `partial` | The named domains, stack and one-Droplet target are represented in configuration and docs. | Hermes/Claude, live Bumpa/Meta and the Droplet deployment are not complete. |
| 22–43, 1. Final product definition | `partial` | Public/user/admin/research routes, FastAPI control-plane routes, tenant models, local chat/sync/report flow. | End-to-end SME onboarding and all provider-backed paths must work on the Droplet. |
| 44–63, 2. Request resolution chain | `partial` | Auth principal resolves membership and tenant; chat stores tenant-scoped messages and research evidence; RLS is present. | Production requests do not yet reach a real Hermes profile or live Bumpa context. WhatsApp has no durable production consumer. |
| 64–88, 3. Domain and surface map | `partial` | One Next.js app, host-aware middleware, Caddy virtual hosts, FastAPI API/webhook. | Production DNS/TLS and complete role-by-host browser tests are pending. |
| 89–147, 4. Deployment architecture | `partial` | Caddy, web, API, Postgres, Redis, backup, and local worker/scheduler Compose services. | Hermes is absent; queue/scheduler are idle local shells and deliberately excluded from production baseline. |
| 148–165, 5. Technology choices | `partial` | Next.js/TypeScript, FastAPI/Python 3.12, Postgres 16, Redis 7, Compose and Caddy are present. | No RQ/Celery queue, Hermes runtime, HTML/Playwright report renderer, or production exception/metrics service. |
| 166–245, 6. One-shot definition of done | `not-implemented` | Individual local subsets are evidenced below. | The definition requires all capabilities working on the Droplet; provider, infrastructure, and launch gates are open. |
| 246–331, 7. Repository structure | `partial` | Monorepo, apps, infra, scripts, tests and docs exist; worker/scheduler currently share the API image. | Several proposed modules/packages/scripts are absent or flattened. Structure alone is not a blocker where equivalent boundaries are maintained. |
| 332–513, 8. Local workflow and branch policy | `partial` | `CODEX.md`, locked dependencies, Make targets, PR template, CODEOWNERS and CI exist. Codex is not a runtime dependency. | Clean-clone bootstrap evidence and enforced protected-`main` settings are not recorded. There is no production deploy workflow. |

### Sections 9–12: schema, tenancy, API, frontend

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 514–599, 9. Core/auth schema | `implemented-tested` | Explicit Alembic migration; tenant, user, platform role, membership, phone, OTP, auth-session and consent models; migration runs in CI. | Native UUID/CITEXT and the plan's full metadata/check constraint set require a schema decision and migration evidence. |
| 600–704, 9. Bumpa schema | `implemented-tested` | Encrypted connection, sync run/rate-limit metadata, raw response, bounded metric snapshots, complete canonical order fields and order-item model. Migration upgrade/downgrade/upgrade passed on SQLite and Postgres 16. | Bind evidence to the final SHA; define raw retention and prove live reconciliation. |
| 705–747, 9. Hermes schema | `partial` | Profile, agent-message and agent-tool-call records exist. Profile path/port are intentionally nullable only for the mock-to-Hermes transition; the live port has range/uniqueness constraints. | Runtime-backed profile state and tool-call execution/permission evidence must be implemented and tested. Transitional nulls may not qualify a live profile. |
| 748–775, 9. WhatsApp schema | `implemented-tested` | Messages, delivery events and durable webhook-event claim records; uniqueness/dedupe tests. | Production queue ownership, retention, and provider reconciliation remain open. |
| 776–813, 9. Research schema | `partial` | Research events, reports and artifacts exist; report generator deletion semantics now allow a null actor. | Validate retention/expiry, raw-access reason and the full report lifecycle. |
| 814–854, 9. Audit/usage schema | `partial` | Audit logs with IP/user-agent fields, system errors with stack and usage events exist. | Validate request-context population and operational retention. |
| 855–888, 10. Tenant isolation | `implemented-tested` | Application RBAC plus explicit `ENABLE`/`FORCE ROW LEVEL SECURITY`; local non-owner probes covered the original tables and the three additive tenant tables. A NOSUPERUSER/NOBYPASSRLS tenant-a role returned tenant-a rows only. | Make the Postgres role/RLS probe repeatable in CI and extend negative browser/API filter/ID tests. |
| 889–1039, 11. FastAPI modules and route map | `partial` | Auth, tenants, settings, chat, Bumpa, Hermes status, MCP, admin, research and webhook routers are registered. | Exact route-map gaps and incomplete provider/lifecycle operations remain; OpenAPI artifact/client drift is not enforced. |
| 1040–1120, 12. Next.js pages and middleware | `partial` | Every listed top-level page path exists, with host/role middleware, same-origin proxy, API-backed production data, and honest loading/empty/error/disabled states. Fixtures require explicit labelled demo mode. | Complete authenticated browser, keyboard, axe and visual-diff evidence. |

### Sections 13–19: providers, research, admin, settings, security

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 1121–1210, 13. WhatsApp routing/webhook | `implemented-tested` | Raw-body HMAC validation, challenge verification, durable event claim, Meta message dedupe, phone routing, unknown sender handling, delivery events and STOP/START tests. | Fast production acknowledgement currently has no queue consumer; a live callback and load test are required. |
| 1211–1259, 13. Sender/templates | `deferred-provider` | Local messaging recorder supports deterministic tests. | Meta sender, OTP delivery, seven approved templates, service-window policy and live receipts are absent. Production remains disabled. |
| 1260–1454, 14. Bumpa direct service | `deferred-provider` | Provider contract, local implementation, Decimal normalization, error availability, redaction, sync storage and local flow tests. | Implement and canary the direct HTTP adapter for ten analytics datasets plus paginated orders. No Bumpa MCP may be introduced. |
| 1455–1616, 15. Hermes integration | `deferred-provider` | Local agent protocol/runtime and profile metadata model. | No Hermes service/image, profile filesystem, private API process, authenticated HTTP client, SOUL/skills/memory/session lifecycle, restart path, or isolation canary exists. |
| 1617–1767, 16. Research instrumentation | `partial` | Consent-gated reads/events, taxonomy fields, filters, keyed domain-separated pseudonyms, defensive legacy-row re-redaction and basic overview are tested. | Instrument the full event list and 20 overview measures; add latency, outcome, chain and quality evidence. |
| 1768–1808, 16. Reports/exports | `partial` | Role-gated CSV, JSONL and valid minimal PDF artifacts with checksums and local download tests. | Production generation is intentionally disabled without a queue. Implement async jobs, richer report types/sections/charts, HTML/Playwright PDF, expiry and visual QA. |
| 1809–1854, 17. Admin console/API | `partial` | Tenant create/edit/suspend, users, phones, encrypted Bumpa connection, local profile record, errors, sync runs, usage and audit APIs; mutation audit tests. | Complete live onboarding, trigger/restart controls, provider-status/failure views, admin export and end-to-end operator browser tests. |
| 1855–1917, 18. User settings and MCP | `partial` | Profile/team/phone/Bumpa status, MCP registry and connection endpoints with an allowlisted provider enum, encrypted credential field and tool-permission records. | Complete the exact invite/delete routes, OAuth, permission-management/admin-approval workflow, confirmation gates and audit evidence. Connectors remain disabled. |
| 1918–1956, 19. Secrets and PII | `partial` | Production mock rejection, field encryption, secure-cookie option, webhook signature, deep non-mutating order redaction, keyed research pseudonyms, defensive text re-redaction, log discipline and history secret scan in CI. | Versioned key rotation, formal retention/deletion, raw-access reason/expiry, CSRF and artifact expiry are open. |
| 1957–1981, 19. Rate limiting/agent safety | `partial` | OTP cooldown/attempt cap, tenant context reconstruction and compact local context exist. | Redis/IP limits for every listed surface, provider budgets, prompt/tool policy enforcement and production agent canaries are absent. |

### Sections 20–28: runtime, deployment, backup, observability, quality

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 1982–2169, 20. Compose/Caddy | `partial` | Rendered local contract publishes only Caddy; database/cache networks are internal; health checks, volumes and security settings exist. | Hermes is absent. Production baseline excludes worker/scheduler. Render and inspect the exact production environment before deployment. |
| 2170–2262, 21. Images/server exclusion | `implemented-tested` | Digest-pinned multi-stage non-root API/web Dockerfiles remove build/package managers from runtime; local images have zero fixable critical/high findings; production read-only/cap-drop overlay and no Codex runtime reference. | Publish `linux/amd64` immutable images and scan the resulting digests. |
| 2263–2373, 22. DigitalOcean deployment | `partial` | SSH, host inspection/hardening, Docker, firewall, deploy user, immutable-ref deploy and temporary multi-host DNS are verified. | GHCR publication/pull, TLS and the actual deployment transcript remain open; final branded DNS is externally blocked. |
| 2374–2410, 23. Backup/restore | `partial` | Custom-format Postgres dump, export/Hermes archives, manifest, SHA-256 verification, retention, destructive confirmation and local restore evidence. | Timer activation is not production-proven. The unit does not load an offsite hook from `.env.production`; implement a reviewed handoff/credential boundary, alert and remote restore. Full Hermes restore waits for Hermes. |
| 2411–2443, 24. Observability | `partial` | JSON logging option, correlation IDs, health routes, system-error records/admin view, Docker health checks and rotated container logs. | Readiness probes only the database and reports configured provider modes; it does not canary providers. Add exception tracking/metrics and disk, backup, sync, delivery and Hermes alerts. |
| 2444–2501, 25. Reports/exports | `partial` | CSV/JSONL/PDF implemented locally. | DOCX is optional; planned report structure, async generation and polished visual proof are incomplete. |
| 2502–2553, 26. Backend tests | `implemented-tested` | Ruff, formatting, strict mypy, pytest branch coverage gate and migration run are in `make quality`/CI. | Re-run and attach results for the final release SHA; add the specified provider matrix. |
| 2554–2569, 26. Frontend tests | `partial` | Lint, formatting, typecheck, 52 unit/component tests and eight desktop/mobile Playwright checks pass locally. | Enforce a coverage threshold and add authenticated settings/admin/research browser flows, axe, keyboard and visual-diff evidence. |
| 2570–2584, 26. Load/failure tests | `not-implemented` | A few deterministic provider failure/retry branches exist. | The 50-event load, Redis/Postgres restart and disk-near-full alert drills are not evidenced. |
| 2585–2619, 27. Makefile | `implemented-tested` | Locked install, lint, format, type, unit, E2E, integration, Compose, backup and restore targets exist. | Production restore must use the production Compose invocation documented in the runbook, not the local convenience target. |
| 2620–2664, 28. Deployment workflow | `partial` | CI and an immutable GHCR image publication workflow exist. | No GitHub-to-Droplet deploy workflow/environment exists; manual SSH deployment is the current intended path. |

### Sections 29–33: workstreams, launch, operations, references, secrets

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 2665–2890, 29. Workstreams | `partial` | Repo/infra, database/auth, local provider ports, main pages, quality and docs have substantial implementation. | Workstreams C–H are not production-complete; live acceptance criteria remain open. |
| 2891–2917, 30. Launch checklist | `external-blocked` | Local synthetic checks cover selected items. | DNS/TLS, Meta, first live Bumpa sync, first Hermes profile, first live chats, production research evidence, off-host backup and production log/privacy review must all be recorded. |
| 2918–2970, 31. Operating runbook | `partial` | `docs/runbook.md` covers baseline triage, safe disabled modes, local backup/restore, future onboarding and provider incidents. | Exercise it on the Droplet and record operator names, timestamps, recovery objectives and incident/alert destinations. |
| 2971–3031, 32. References | `implemented-tested` | `docs/references.md` records the primary links named by the plan and their intended use. | Pin provider versions/contract snapshots when live adapters are implemented. |
| 3032–3157, 33. Secrets/credentials | `external-blocked` | Examples contain placeholders only; CI scans repository history. Anthropic values are excluded from the shared app environment. | Generate host-only application/database secrets; authorize deploy/pull credentials; obtain DNS/offsite access; later obtain Meta, Anthropic-through-Hermes and per-tenant Bumpa credentials. Never commit values. |

## Acceptance matrices

### Production infrastructure baseline

The following gates are necessary before calling even the provider-disabled baseline
deployed. They do **not** authorize real users.

| Gate | Required artifact | Current status |
|---|---|---|
| Final revision quality | CI URL plus JUnit/coverage/Playwright artifacts for the exact SHA | Pending final revision |
| Immutable images | API/web digests tagged `sha-<full-sha>`, provenance/SBOM, vulnerability result | Pending publication/scan |
| SSH | Non-root deploy user accepts the exact intended key; root key-only policy recorded | Complete — fingerprint `SHA256:+n9DH8aIPVN/Rcwqx35jc4+FmoKzDB8/lcaE2222MxQ` |
| Host hardening | OS, capacity, firewall, updates, Docker and published-port transcript | Complete for the host baseline; application port proof follows deployment |
| DNS/TLS | A/AAAA resolution and certificate checks for public, `www`, API, admin and research | `external-blocked` |
| Production environment | Mode `production`, mock/demo rejected, providers `disabled`, async runtime false, file mode 0600 | Pending host configuration |
| Migration/start | Migration transcript and healthy Caddy/web/API/Postgres/Redis on immutable images | Pending deployment |
| Negative provider canary | OTP, webhook/provider sync/chat/report generation fail closed instead of using mocks | Pending production test |
| Local backup | Backup ID, manifest, checksum and timer journal | Pending production test |
| Off-host durability | Remote object ID/checksum and isolated restore result | `external-blocked` |

### Provider activation gates

No backend may move from `disabled` to its live selector merely because credentials
exist.

| Provider | Must be complete before activation |
|---|---|
| Meta WhatsApp | Credential validation; versioned sender; approved templates; verified callback; durable queue/outbox; signature/dedupe/rate-limit tests; unknown/STOP behavior; live test recipient and delivery receipt; alert/runbook. |
| Bumpa | Allowlisted direct base URL; encrypted per-tenant key; scope verification; all ten analytics datasets plus paginated orders; bounded retries/limits; canonical/raw reconciliation; nested redaction; live sandbox transcript; alert/runbook. |
| Hermes/Claude | Pinned Hermes image; private topology; per-tenant profile filesystem/process/auth; Hermes-only Anthropic secret; health/restart; compact redacted context; cross-profile isolation; timeout/budget handling; backup/restore; live canary. |
| Async jobs/reports | Redis queue, transactional handoff/outbox, idempotency, bounded retries/dead-letter visibility, worker/scheduler health, failure/restart tests, alerts and operator replay policy. |

## Evidence record template

Add a row to `docs/verification.md` or the protected external evidence system for
every environment claim:

```text
Claim:
Environment: contract | local | live | production
UTC timestamp:
Git revision:
Image digest(s):
Command or test ID:
Redacted result/artifact URL:
Operator/reviewer:
Expiry or revalidation condition:
```

Do not paste access tokens, cookies, phone numbers, raw provider payloads, customer
data, `.env.production`, SSH private keys, or decrypted API keys into this ledger.
