# Production release evidence — c0c1544

This is the redacted operator record for application release
`c0c15443352ab84fde1d2edfde1ed0692ed842f6`, promoted and verified on
2026-07-15. The later commit that adds this document is evidence-only and is not a
separately promoted application revision.

This record contains no credentials, authentication values, phone mappings,
tenant/user/profile/session/job/conversation identifiers, origin address,
administrator network range, secret path, environment value, raw provider payload,
business/customer/product label, business total or backup-directory identifier.

## Release chain

- Brand/SEO and Bumpa data hardening
  [PR 49](https://github.com/makriman/bumpa-rct-app/pull/49), store-boundary and
  persistence [PR 51](https://github.com/makriman/bumpa-rct-app/pull/51), and the
  final slow-product timeout
  [PR 52](https://github.com/makriman/bumpa-rct-app/pull/52) are included. PR 52
  merged as the deployed application revision
  `c0c15443352ab84fde1d2edfde1ed0692ed842f6`.
- PR [CI 29412255491](https://github.com/makriman/bumpa-rct-app/actions/runs/29412255491)
  passed 13/13 jobs on the release-equivalent tree.
- Exact-main [CI 29412671738](https://github.com/makriman/bumpa-rct-app/actions/runs/29412671738)
  passed 13/13 jobs.
- Exact-main [publication 29413085773](https://github.com/makriman/bumpa-rct-app/actions/runs/29413085773)
  passed 7/7 jobs. Publication gates and scans passed for all six images; every
  image carries the exact application revision label.

| Service              | Deployed OCI index reference                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------- |
| API/worker/scheduler | `ghcr.io/makriman/bumpabestie-api@sha256:e36d5880d708f9e2938fe073811141030eaf1d2203def00d029d217493030729`      |
| Web                  | `ghcr.io/makriman/bumpabestie-web@sha256:b7e7af11086811b4b21b51d8f84e9f6e9a252d0cc433772d79119abd79266a90`      |
| Caddy                | `ghcr.io/makriman/bumpabestie-caddy@sha256:98db47f82070d9b8d6728271f9f535d185439094c429cce8b1bf6c4c2f59f55a`    |
| PostgreSQL           | `ghcr.io/makriman/bumpabestie-postgres@sha256:787e1561b1a868385a8ca2bd929f70990fd3962605fee690acdb09381dd7e8d0` |
| Backup               | `ghcr.io/makriman/bumpabestie-backup@sha256:95ab1e6bfea93a7760312a4507586dcf7e7d9fdc917a5aa264f42a7febc747ef`   |
| Hermes               | `ghcr.io/makriman/bumpabestie-hermes@sha256:15bee9faa8be8c229304e05aa5bd44966090528ce13646f0cd78849e495b96e9`   |

The deployed release record, live container references, registry indexes and OCI
revision labels match this exact revision and these exact digests. Redis matches
its separately pinned upstream digest.

## Local and CI quality

The complete local `make quality` gate passed before merge:

- API lint, formatting and strict typing; 483 tests passed, one skipped, with
  85.75% branch coverage;
- web formatting, linting, type checking, production build and 171 tests;
- 79 operations tests;
- migration, OpenAPI/generated-TypeScript drift, Compose, production-contract,
  browser, accessibility, asset and security checks.

Focused adapter/degraded tests passed 119 checks, production helper tests passed 25
checks, and focused brand/SEO tests passed 21 checks.

## Guarded promotion and runtime boundary

- Root-owned promotion prerequisites were installed byte-for-byte from the exact
  target revision and passed syntax and narrow-privilege validation.
- The coordinator pulled only the six exact indexes above, stopped writers,
  created and verified its pre-promotion recovery point, migrated transactionally,
  reconciled five Hermes profiles, recreated the intended services and passed
  origin/public smoke.
- The eight long-running services are Caddy, web, API, worker, scheduler, Hermes,
  PostgreSQL and Redis. Seven have configured healthchecks and report healthy;
  Caddy is intentionally the service without a Docker healthcheck.
- Readiness reports database, Redis, worker and scheduler healthy, Bumpa selected,
  Hermes selected and WhatsApp disabled.
- Schema is `0015_bumpa_store_context`. The production environment file retains
  its restrictive ownership/mode; backup, disk and firewall units are active; the
  Cloudflare-only firewall and maintenance interlocks pass.

## Mapping and temporary web authentication

The redacted onboarding audit passed with five tenants, five owners, five owner
memberships, five approved phone identities, five active Bumpa connections and one
explicitly approved operator/owner dual role.

The production authentication matrix exercised every approved collaborator on all
three host-scoped surfaces: public chat, administration and research. All 15
sign-ins passed.

- Challenge requests returned the provider-free public shape without exposing a
  developer code.
- Verification kept the access token server-side and produced a Secure, HttpOnly,
  SameSite=Lax, path-rooted, host-only cookie.
- Membership and independent admin/research role gates held on every surface;
  authentication did not grant or modify a role.
- Sibling hosts did not inherit the cookie; wrong-surface routes redirected to the
  authorized surface.
- Logout revoked each session. An unmapped reserved canary received the same
  challenge shape and a generic verification denial.
- Final read-only reconciliation found five mapped users with all three intended
  access aggregates unchanged, zero active sessions and zero active temporary
  challenges.

WhatsApp authentication and delivery remained disabled throughout. No Meta send,
OTP or receipt is claimed.

## Bumpa adapter and persistence acceptance

The strict five-store canary proved the new `products.overview` read policy fixes
the previously slow third store. Stores 1–4 each completed accepted partial with
eight available analytics datasets, the provider's two typed profit limitations,
orders available and no dataset error.

Store 5's direct one-attempt exact-endpoint probe produced a typed provider-limit
timeout with no HTTP response. The scoped canary helper then reached its fixed
240-second polling deadline while waiting for that store. The deadline was not
widened. Its durable job finished successfully seconds later, and read-only durable
evidence proved the allowed result: partial/degraded, orders available, the same two
typed unavailable profit datasets, and `products.overview` as the sole dataset
error. This is an honestly degraded provider result, not a fabricated success.

Read-only schema-0015 reconciliation passed across all five active connections:

- four accepted-partial and one degraded current run;
- 50 current raw analytics rows and 50 current metric snapshots;
- complete order page sets, with canonical order and item counts/payloads
  reconciled;
- valid store and per-order currencies, including retained historical currency;
- exact store-local inclusive 30-day boundaries and response ranges;
- accepted-partial runs advance success freshness, while the degraded run records
  failure freshness without advancing last success;
- current redaction is idempotent and older connection-boundary rows are excluded
  from current consumers.

No Bumpa MCP is used. Missing or failed provider values remain unavailable, never
zero.

## Hermes/Claude acceptance

Five owner-scoped live chat canaries used a synthetic prompt through five distinct
active Hermes profiles. Normal tenant-scoped redacted context remained inside the
Hermes boundary; prompt/response bodies were omitted from evidence. Read-only
reconciliation found five conversations, five
inbound messages, five non-empty outbound messages, Hermes usage attribution, zero
tool calls, zero new Hermes errors, and zero active canary sessions after cleanup.

Five authenticated same-profile health probes passed. All 20 cross-profile
gateway credential attempts were rejected. Together with the separate runtime,
filesystem and context-isolation contracts, this supports the five-profile
boundary without treating the GET probes as comprehensive isolation proof. No
prompt, response text, credential, profile coordinate or conversation identifier
was retained in this record.

## Brand, metadata and browser acceptance

The live release passed exact HTTP/source-hash verification for the public brand
and discovery surface:

- five public pages with route-specific canonical, Open Graph and Twitter metadata;
- homepage nonce-bearing JSON-LD;
- robots policy and a sitemap containing exactly the five public URLs;
- PWA manifest and seven logo/favicon/social assets whose live SHA-256 values match
  the reviewed source assets;
- no-index response headers on login, workspace, admin and research surfaces.

The selected in-app browser rechecked the live homepage and sign-in flow at desktop
and mobile widths. Both were free of horizontal overflow and console warnings or
errors. The live sign-in control exposes a searchable, accessible country-code
listbox with rendered flag assets, calling codes and a usable mobile telephone
field/action. The sanitized DOM/console/hash transcript is
`artifacts/design-qa/live-browser-c0c1544.json`; exact local design captures remain
in `artifacts/design-qa/`, and the source/reference comparison is recorded in
`design-qa.md`. Live screenshot capture was not retained.

## Post-release guarded backup

After the provider, Hermes and authentication canaries, the systemd backup wrapper
completed with a successful service result and exit status. It quiesced and resumed
the writers and left all eight services running with all seven configured
healthchecks healthy.

The resulting recovery point contains exactly six files: four data archives/dump,
the manifest and `SHA256SUMS`. The checksum file contains five entries because it
covers the four data files plus the manifest; every SHA-256 value replayed
successfully. The format-3 manifest binds the exact application revision, schema
`0015_bumpa_store_context`, the exact backup image above and PostgreSQL/pg_dump
16.14; it includes PostgreSQL, exports, Hermes runtime and Hermes staging.

This closes the exact-release local recovery-point gate only. Encrypted off-host
copy and isolated remote restore evidence remain open.

## Stability and final smoke

The stability clock started only after the final guarded backup. After 20 full
minutes, five live samples at 60-second spacing retained the same eight container
identities and exact images. Every sample reported all eight services running, all
seven configured healthchecks healthy, zero restarts, zero OOM kills, schema
`0015_bumpa_store_context` and healthy database/Redis/worker/scheduler readiness.

The bounded post-backup log review found no severe, crash, OOM or exit-signal
signature. Final direct-origin and public-edge smoke passed. Backup and disk timers
were enabled/active; the Cloudflare-only firewall service/state and UFW were active;
the coordinator, maintenance lock state and interlocks were clear.

Two preliminary verifier invocations stopped before production assertions because
of operator-side invocation errors: one parsed the four-field legacy marker as a
bare SHA, and one omitted the explicit branded-domain arguments. Both verifiers
were corrected and rerun completely. No production assertion failed.

## External and residual gates

This release is live and usable within its recorded contained scope. It does not
authorize unrestricted provider operation. The remaining gates are explicit:

- complete Meta Business/sender/template verification and separately prove
  WhatsApp authentication and delivery before enabling that channel;
- obtain a successful current `products.overview` response for Store 5 before
  calling Bumpa coverage complete;
- configure and exercise encrypted off-host backup and an isolated restore;
- configure and verify a real external alert destination and signed receipt; and
- obtain formal privacy/security/retention approval before unrestricted launch.
