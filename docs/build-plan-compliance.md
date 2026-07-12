# Build-plan compliance and evidence ledger

## Purpose

This ledger maps `bumpabestie-buildplan.md` to the repository as it exists. It is
an implementation audit, not a replacement specification and not a launch
certificate. A source file proves that code exists; only a passing test or an
environment-specific transcript proves that it works in that environment.

The plan's one-shot definition of done explicitly requires the complete product on
the DigitalOcean Droplet. That definition is **not yet met**. The repository now has
the production Meta, direct Bumpa, Hermes, durable async, retention, and security
implementation with complete local contract/smoke evidence. The currently deployed
revision is still the historical provider-disabled infrastructure baseline and must
not be mistaken for this pending release.

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
| Local developer product path | `implemented-tested` | Compose, migrations through `0004`, synthetic OTP, durable jobs, local commerce sync, browser chat, API-backed user/settings/admin/research views, signed WhatsApp fixtures, research retention, and asynchronous CSV/JSONL/PDF artifacts have local evidence. | Production provider, load/failure and recovery evidence remains. |
| Production infrastructure baseline | `production-baseline-disabled` | Hardened release `54bb8e9b29295171d65972e094e508d25a7bc53d` is live on five TLS-enabled sslip.io hosts. It runs exact-digest Caddy, web, API, Postgres and Redis images with zero restarts and providers disabled; workers/scheduler/Hermes are absent. | Branded DNS, off-host durability, provider activation and their launch evidence remain open. |
| WhatsApp/Meta | `partial` | HMAC verification, durable acknowledgement/processing, dedupe, routing, STOP/START, delivery-state handling, Meta sender, idempotency, CSRF/rate limits, and retry/ambiguous-send behavior have deterministic tests. The supplied Meta token/app/WABA were validated. | Verify/register the phone, subscribe the callback, obtain approved templates, and record inbound/outbound delivery receipts and failure canaries. |
| Hermes/Claude | `partial` | The pinned upstream-derived Hermes image, six-image stack, Hermes-only secret mount, authenticated private per-profile gateway, staging/reconciliation, client, lifecycle and isolation contracts are implemented and tested. | Provision the five production profiles and record Claude, restart, cross-profile, backup, and recovery canaries. |
| Bumpa | `partial` | The direct adapter, encrypted tenant keys, durable sync jobs, ten analytics datasets plus paginated orders, bounds/retries, Decimal handling, raw/canonical redaction and reconciliation are implemented and tested. Four credentials completed every live canary; the fifth passed all but one transient overview request. | Onboard all five tenants and record complete production sync/reconciliation evidence, including a successful retry of the transient endpoint. |
| Off-host durability | `external-blocked` | Production backup `20260712T140353Z` is checksum-verified and the nightly systemd timer is enabled; isolated local restore evidence also exists. | Select and credential an encrypted off-host target, wire the exact latest-backup handoff, alert on both stages, and restore from that target. |
| Droplet and domains | `partial` | The key is authorized for root and the non-root deploy user; Ubuntu 24.04, Docker, UFW, fail2ban, unattended upgrades and swap are verified. Five temporary sslip.io hosts have valid TLS and route the hardened provider-disabled release. | The planned `bumpabestie.com` remains unregistered and is not launch-ready. |

## Section-by-section build-plan audit

### Sections 1–8: product, architecture, surfaces, workflow

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 1–21, project identity/target | `partial` | The named domains, stack and one-Droplet target are represented in configuration and docs; a provider-disabled baseline is live on temporary hosts. | Hermes/Claude, live Bumpa/Meta, branded DNS and the complete product deployment are not complete. |
| 22–43, 1. Final product definition | `partial` | Public/user/admin/research routes, FastAPI control-plane routes, tenant models, local chat/sync/report flow. | End-to-end SME onboarding and all provider-backed paths must work on the Droplet. |
| 44–63, 2. Request resolution chain | `partial` | Auth resolves membership/tenant, chat builds redacted Bumpa context and routes through the selected profile, WhatsApp uses durable jobs, and RLS protects tenant records. | Bind and canary the five production tenant/profile contexts. |
| 64–88, 3. Domain and surface map | `partial` | One Next.js app, host-aware middleware, Caddy virtual hosts, FastAPI API/webhook and five TLS-enabled temporary production hosts. | Branded DNS/TLS and complete role-by-host browser tests are pending. |
| 89–147, 4. Deployment architecture | `implemented-tested` | Caddy, web, API, worker, scheduler, private Hermes, Postgres, Redis, backup, six exact images, internal networks and scoped secrets are represented and contract-tested. | Deploy and record the exact pending release. |
| 148–165, 5. Technology choices | `partial` | Next.js/TypeScript, FastAPI/Python 3.12, Postgres 16, Redis 7, durable in-house jobs/outbox, Hermes, Compose and Caddy are present. | A production exception/metrics destination and richer HTML/Playwright report renderer remain. |
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
| 1121–1210, 13. WhatsApp routing/webhook | `implemented-tested` | Raw-body HMAC validation, challenge verification, durable event claim/job, Meta message dedupe, phone routing, unknown sender handling, delivery events, STOP/START, retries and Redis limits are tested. | A live callback, delivery receipt and load test are required. |
| 1211–1259, 13. Sender/templates | `partial` | The Meta sender implements template/text policy, idempotency, response bounds and an ambiguous-send guard; OTP and job tests pass. Six non-authentication templates were submitted and remain provider-pending. | Verify/register the phone, obtain template approval and record live OTP/message receipts. The authentication template is blocked by current app permission. |
| 1260–1454, 14. Bumpa direct service | `partial` | Direct HTTP adapter, ten analytics datasets plus paginated orders, encryption, Decimal normalization, unavailable/error semantics, limits/retries, deep redaction, durable sync and canonical/raw reconciliation are tested. Four live credentials completed all calls; the fifth had one transient overview timeout. | Retry the remaining canary and record all five production tenant syncs. No Bumpa MCP may be introduced. |
| 1455–1616, 15. Hermes integration | `partial` | Pinned upstream-derived image, private authenticated gateways, profile staging/reconciliation, Hermes-only Anthropic secret, typed client, lifecycle, health and isolation contracts are implemented. | Provision the five production profiles and record Claude, restart, isolation, backup and recovery canaries. |
| 1617–1767, 16. Research instrumentation | `partial` | Consent-gated reads/events, taxonomy fields, filters, keyed domain-separated pseudonyms, defensive legacy-row re-redaction, overview measures, and privacy-safe conversation grouping/detail are tested. | Complete the full 20-measure catalogue and add latency, outcome, chain and quality evidence. |
| 1768–1808, 16. Reports/exports | `partial` | Role-gated asynchronous CSV, JSONL and valid PDF artifacts with checksums, expiry, cleanup, consent invalidation and download-authorization tests. | Richer report sections/charts and production visual evidence remain. |
| 1809–1854, 17. Admin console/API | `partial` | Tenant create/edit/suspend, users, phones, encrypted Bumpa connection, local profile record, errors, sync runs, usage and audit APIs; mutation audit tests. | Complete live onboarding, trigger/restart controls, provider-status/failure views, admin export and end-to-end operator browser tests. |
| 1855–1917, 18. User settings and MCP | `partial` | Profile/team/phone/Bumpa status, MCP registry and connection endpoints with an allowlisted provider enum, encrypted credential field and tool-permission records. | Complete the exact invite/delete routes, OAuth, permission-management/admin-approval workflow, confirmation gates and audit evidence. Connectors remain disabled. |
| 1918–1956, 19. Secrets and PII | `partial` | Production mock rejection, scoped secret files, field encryption, secure cookies, cookie-origin CSRF, webhook signatures, deep redaction, keyed pseudonyms, raw-access reason/audit gates, consent invalidation, artifact expiry and cleanup are tested. | Versioned key rotation, formal privacy/retention approval, and off-host secret/backup operations remain. |
| 1957–1981, 19. Rate limiting/agent safety | `partial` | Redis-backed HMAC phone/IP OTP limits and separate privacy-preserving budgets for web chat, WhatsApp chat, Bumpa sync and research reports are enforced in production. Tenant context reconstruction, redacted compact context, bounded provider clients and fail-closed behavior are tested. | Prompt/tool policy expansion and production agent canaries remain. |

### Sections 20–28: runtime, deployment, backup, observability, quality

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 1982–2169, 20. Compose/Caddy | `partial` | The pending six-image contract includes Caddy, web, API/worker/scheduler, private Hermes, Postgres, Redis and backup; only Caddy publishes ports, data networks are internal, and health/secret boundaries are tested. The historical deployed release is still the five-service disabled baseline. | Deploy and verify the pending service topology. |
| 2170–2262, 21. Images/server exclusion | `implemented-tested` | The historical five images were published and scanned; they remain deployed. The pending `linux/amd64` contract covers six images, pinned Hermes and service-scoped runtime initialization. Local provenance/SBOM and exact-digest scan gates report zero fixable high/critical findings. | Publish and scan all six exact registry digests for the merged revision. |
| 2263–2373, 22. DigitalOcean deployment | `partial` | Hardened exact-digest release `54bb8e9b29295171d65972e094e508d25a7bc53d` is deployed: five healthy services, zero restarts, current Caddy/PostgreSQL/Redis versions, disabled readiness, negative canaries, temporary DNS/TLS and desktop/mobile visual checks passed. | Final branded DNS and full provider-backed production deployment remain externally blocked. |
| 2374–2410, 23. Backup/restore | `partial` | Restricted-capability backup `20260712T140353Z` has a verified historical format-2 manifest/checksum. The pending format-3 contract quiesces writers, preserves Postgres/exports/Hermes runtime+staging, resumes services on failure, and narrowly reads the optional offsite hook path. Isolated restore passes locally. | Configure an encrypted off-host target, alert both stages, and prove a remote restore; Caddy state also needs a reviewed snapshot/backup policy. |
| 2411–2443, 24. Observability | `partial` | JSON request/job logs, correlation IDs, database plus Redis/worker/scheduler readiness, system-error records/admin view, Docker health checks and rotated logs are implemented. | Readiness reports provider selectors but does not live-canary providers. Add exception tracking/metrics and external disk, backup, sync, delivery and Hermes alert destinations. |
| 2444–2501, 25. Reports/exports | `partial` | CSV/JSONL/PDF implemented locally. | DOCX is optional; planned report structure, async generation and polished visual proof are incomplete. |
| 2502–2553, 26. Backend tests | `implemented-tested` | Ruff, formatting, strict mypy, migrations through `0004`, 111 tests and 86.73% branch-aware coverage pass locally, including provider, durable lease, job, privacy and security matrices. | Bind the same gates to the final merged SHA. |
| 2554–2569, 26. Frontend tests | `partial` | Lint, formatting, typecheck/build, 78 unit/component tests across 15 files, and ten desktop/mobile Playwright checks pass locally; keyboard navigation was manually inspected. | Bind to final CI, enforce coverage threshold, and add automated axe/visual-diff evidence. |
| 2570–2584, 26. Load/failure tests | `not-implemented` | A few deterministic provider failure/retry branches exist. | The 50-event load, Redis/Postgres restart and disk-near-full alert drills are not evidenced. |
| 2585–2619, 27. Makefile | `implemented-tested` | Locked install, lint, format, type, unit, E2E, integration, Compose, backup and restore targets exist. | Production restore must use the production Compose invocation documented in the runbook, not the local convenience target. |
| 2620–2664, 28. Deployment workflow | `partial` | CI and an immutable GHCR image publication workflow exist. | No GitHub-to-Droplet deploy workflow/environment exists; manual SSH deployment is the current intended path. |

### Sections 29–33: workstreams, launch, operations, references, secrets

| Plan lines / section | Status | Repository evidence | Open requirement / exit evidence |
|---|---|---|---|
| 2665–2890, 29. Workstreams | `partial` | Repo/infra, database/auth, local provider ports, main pages, quality and docs have substantial implementation. | Workstreams C–H are not production-complete; live acceptance criteria remain open. |
| 2891–2917, 30. Launch checklist | `external-blocked` | Local synthetic checks cover selected items, and temporary-host TLS is proven for the provider-disabled baseline. | Branded DNS/TLS, Meta, first live Bumpa sync, first Hermes profile, first live chats, production research evidence, off-host backup and production log/privacy review must all be recorded. |
| 2918–2970, 31. Operating runbook | `partial` | Baseline diagnostics, deployment verification, negative OTP/docs canaries and production backup/timer operations were exercised on the Droplet; local destructive restore is tested. | Record incident destinations and recovery objectives; exercise off-host restore after storage is selected. |
| 2971–3031, 32. References | `implemented-tested` | `docs/references.md` records the primary links named by the plan and their intended use. | Pin provider versions/contract snapshots when live adapters are implemented. |
| 3032–3157, 33. Secrets/credentials | `external-blocked` | Examples contain placeholders only; CI scans repository history. Anthropic values are excluded from the shared app environment. | Generate host-only application/database secrets; authorize deploy/pull credentials; obtain DNS/offsite access; later obtain Meta, Anthropic-through-Hermes and per-tenant Bumpa credentials. Never commit values. |

## Acceptance matrices

### Production infrastructure baseline

The following gates are necessary before calling even the provider-disabled baseline
deployed. They do **not** authorize real users.

| Gate | Required artifact | Current status |
|---|---|---|
| Final revision quality | CI URL plus JUnit/coverage/Playwright artifacts for the exact SHA | Complete — PR CI 29194474957 and merged-main CI 29194621699 for `54bb8e9b29295171d65972e094e508d25a7bc53d` |
| Immutable images | Five API/web/Caddy/PostgreSQL/backup digests tagged `sha-<full-sha>`, provenance/SBOM and exact-digest vulnerability results | Complete — five exact index refs and scan evidence from publish run 29194814472 |
| SSH | Non-root deploy user accepts the exact intended key; root key-only policy recorded | Complete — fingerprint `SHA256:+n9DH8aIPVN/Rcwqx35jc4+FmoKzDB8/lcaE2222MxQ` |
| Host hardening | OS, capacity, firewall, updates, Docker and published-port transcript | Complete for the hardened live baseline |
| DNS/TLS | A/AAAA resolution and certificate checks for public, `www`, API, admin and research | Temporary sslip.io baseline complete; branded domain `external-blocked` |
| Production environment | Mode `production`, mock/demo rejected, providers `disabled`, async runtime false, file mode 0600 | Complete for `54bb8e9b29295171d65972e094e508d25a7bc53d` |
| Migration/start | Migration transcript and healthy Caddy/web/API/Postgres/Redis on immutable images | Complete — exact five services, zero restarts; Caddy 2.11.4, PostgreSQL 16.14 and Redis 7.4.9 |
| Negative provider canary | OTP, webhook/provider sync/chat/report generation fail closed instead of using mocks | OTP 503 and production docs-negative probe complete; authenticated provider matrix remains pending |
| Local backup | Backup ID, manifest, checksum and timer journal | Complete — `20260712T140353Z`, format 2/checksum/revision/image verified; timer enabled |
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
