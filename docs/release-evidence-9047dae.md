# Production SME consultant release evidence — 9047dae

This is the redacted operator record for application release
`9047dae7f884ee953de842c2d8bc8c456e48ae7a`, promoted and verified on
2026-07-25. The later Worker-only merge and this operations/evidence follow-up
are not separate application-stack promotions.

This record contains no credentials, phone mappings, tenant/user/profile/session
identifiers, participant content, hidden prompts, raw provider payloads,
business/customer/product labels, business totals, secret paths, origin address
or backup-directory identifier.

## Release chain

- Production SME consultant capabilities were implemented in
  [PR 70](https://github.com/makriman/bumpa-rct-app/pull/70), which merged as
  application revision `9047dae7f884ee953de842c2d8bc8c456e48ae7a`.
- Exact-main
  [CI 30165914178](https://github.com/makriman/bumpa-rct-app/actions/runs/30165914178)
  passed every required job for the deployed application tree.
- Exact-main
  [publication 30166552591](https://github.com/makriman/bumpa-rct-app/actions/runs/30166552591)
  published and scanned all eight immutable application and infrastructure
  indexes.
- A live Sandbox command exposed a missing `/workspace/.tmp` initialization.
  [PR 71](https://github.com/makriman/bumpa-rct-app/pull/71) fixed it and merged
  as `68f9f988208b6a460269d0760abba8c82367f9f1`. The corrected Worker source was
  deployed separately as Cloudflare Worker version
  `0f5a78d1-660a-41e9-b75d-a970c5933330`.
- Follow-up
  [main CI 30167202934](https://github.com/makriman/bumpa-rct-app/actions/runs/30167202934)
  passed the complete quality, browser, resilience and eight-image security
  matrix for the Worker-fix tree.

| Service | Deployed OCI index reference |
| --- | --- |
| API/worker/scheduler | `ghcr.io/makriman/bumpabestie-api@sha256:92d4f65907c2830fec5a7c221ae47a2bca6bdb0fce12a59968b47bf2d0bfc2e4` |
| Consumer web | `ghcr.io/makriman/bumpabestie-web@sha256:f16b6c633afa2a89179c71912fed907c394a14c312bf977a607f3f3adf3f1a5e` |
| Admin web | `ghcr.io/makriman/bumpabestie-admin-web@sha256:7b2078178d03487f49c8bec51a92d50642e27d257edcf866cb44a8375c5e00d7` |
| Research web | `ghcr.io/makriman/bumpabestie-research-web@sha256:f1cd16cd143b234b9ad137eda297ec4d7029e83f791b62fab58f3e81c00d87f0` |
| Caddy | `ghcr.io/makriman/bumpabestie-caddy@sha256:1cb834dc5274e432138601aa15567dfa9ea0ad45e8c2a0dcb7d751a22859e21c` |
| PostgreSQL | `ghcr.io/makriman/bumpabestie-postgres@sha256:a4d424fe4957ee1962bfb234fb5877fe563b68269768e31de0e511d80e562026` |
| Backup | `ghcr.io/makriman/bumpabestie-backup@sha256:35c31bdc194441034a86d2f28e55263fa0c335b4a1557188234bad87ef924951` |
| Hermes | `ghcr.io/makriman/bumpabestie-hermes@sha256:5c61ad59db13d20e12583a24abbdf634a8841c68c872c71f64aab262d80122ac` |

The production checkout, release journal and application release record match
`9047dae7f884ee953de842c2d8bc8c456e48ae7a`. The Worker-only fix does not
change any of the eight application-stack indexes.

## Local and CI quality

The complete local quality gate passed before promotion:

- API lint, formatting and strict typing; 576 tests passed, one skipped, with
  85.04% branch coverage;
- consumer web 120 tests, admin web 34 tests and research web 18 tests;
- 97 operations tests plus the Sandbox Worker test suite;
- OpenAPI/generated client drift, migration/RLS, Compose, deployment,
  security, accessibility, browser and resilience gates;
- React Doctor with zero diagnostics and passing Lighthouse budgets.

Both exact-main CI runs passed. Their image jobs built and scanned the API,
consumer web, admin web, research web, Caddy, PostgreSQL, backup and Hermes
images on `linux/amd64`.

The operations/evidence follow-up passed `make quality`: 576 API tests, 172
frontend tests, 101 operations tests, three Sandbox Worker tests, strict typing,
contract drift, production builds, Lighthouse budgets and the production
configuration contract. Its new content-free activation report also passed
ShellCheck 0.10.0.

## Grounded consultant and business tools

The summary-only policy was replaced by the production SME consultant policy.
It covers business planning, marketing, sales, operations, finance, expansion
and general assistance while distinguishing:

- values calculated from tenant-scoped Bumpa data;
- external claims and original source URLs;
- inference, uncertainty, recommendations and provider/data gaps.

Supported model requests use deterministic temperature. Store-local periods
carry exact inclusive date bounds, currency, freshness, inclusion rules and
coverage warnings. Provider-incompatible temperature is omitted rather than
causing a failed request.

Five live synthetic Claude canaries completed through five distinct active
Hermes profiles. Each profile exposes the same 19 managed tools with a different
tenant-bound MCP credential. The credential resolves the tenant server-side;
Claude cannot provide or select a tenant ID. All canary answers were non-empty,
and cleanup revoked every canary session.

A separate live delegation canary persisted a `delegate_task` call, completed
one leaf calculation, emitted two tool lifecycle events and returned the
expected parent result. The provider session was deleted. A scheduled-task
canary then created and triggered a local-only Hermes cron job in one profile.
The cron agent called the tenant-bound `calculate_exactly` MCP tool exactly once,
returned the expected result and removed its own job; final inspection found
zero residual cron jobs. Unsafe native terminal/browser/file/network and
messaging tools remained unavailable to both paths.

The business-data tools cover profile/coverage, overview and comparisons, sales
and profit trends, order status/channel, products and mix, aggregated customer
segments, supported inventory, calculator/reconciliation and safe prepared
connector actions. Tool results are structured and bounded; raw customer
personal data is not returned.

A production period canary resolved “last week” to `2026-07-13` through
`2026-07-19` in the store timezone. Its follow-up conversation retained context
and correctly understood “Yes, give me that template.”

## Research status and Ghana regression

The Tavily search/extraction boundary, query privacy rules, citation handling,
untrusted-content treatment and outage behavior are implemented and tested.
Tavily is not activated in production because no production key is provisioned.

The live Ghana expansion regression therefore combined available business
figures, currency and exact date context with an explicit research-provider
outage. It returned no fabricated URL or citation. A successful current,
externally cited Ghana analysis remains an open provider activation gate; this
record does not claim it.

## Isolated Sandbox and managed media

Terminal, Python/JavaScript/TypeScript execution, files, video frames, PDF page
rendering and outbound sanitization run in a tenant-and-workspace-derived
Cloudflare Sandbox. The runtime receives no production mounts, database
credentials or Hermes profile secrets, and direct internet access is disabled.

The authenticated live smoke proved:

- missing/invalid authentication is rejected;
- direct egress is denied and health reports `internet_access: false`;
- isolated file write/read and text export;
- PDF generation, sanitization and first-page rendering;
- video/PDF dependencies including FFmpeg, Ghostscript and Poppler;
- tenant isolation and explicit Sandbox cleanup.

After the Worker temp-directory fix, the same smoke passed without a workaround.
A separate synthetic managed-image canary returned HTTP 200 with a valid PNG
from `@cf/black-forest-labs/flux-1-schnell`, marked the result as untrusted and
discarded the generated bytes without participant delivery.

## WhatsApp and Meta boundary

The primary Cloud API sender and the five mapped pilot identities are enabled
behind Bumpa Bestie’s signature-verifying phone-to-tenant router. The deployed
capability switches enable the primary-number pilot, multimodal processing,
typing/progress behavior and managed media. OTP, public onboarding and proactive
templates remain disabled.

Read-only Meta evidence shows the primary sender is code-verified and uses the
Cloud API. The daily and weekly marketing templates are approved. No approved
OTP template exists, so WhatsApp OTP remains disabled. The configured Meta app
is subscribed to the WABA.

A public-boundary canary proved:

- an invalid webhook signature returns 403;
- a valid signed synthetic payload returns 200;
- replay is deduplicated and the durable event is marked ignored;
- no outbound WhatsApp message is created.

Contract and integration tests cover text/captions, quoted replies, interactive
responses, images, audio, voice, video, PDFs/documents, stickers, location and
contacts; corrupt/unsupported/oversized fallbacks; immediate read/typing work;
bounded real-tool progress; FIFO locking; duplicate IDs; monotonic delivery
status; chunking; native media/voice delivery and the 24-hour template boundary.

No participant message or real media send was performed during acceptance.
Research consent remains pending for all five participants, and no separately
approved internal WhatsApp test recipient was available. Real text, image,
voice, document, video and delivery/read receipts therefore remain an explicit
consented canary gate rather than a production claim.

## Connectors, confirmations and dormant Home Assistant

The Google Drive, Sheets, Gmail, Calendar, Meta Ads and Home Assistant connector
registry is implemented behind server-side tenant connections, resource
allowlists and tool permissions. Approved reads may execute automatically.
Writes can only become pending actions with an exact preview.

Confirmation is tenant-bound, user-bound, expiring, single-use and idempotent.
Claude has no execute-write tool. Production has no active connector
connections and no pending write actions because Google and Meta Ads OAuth
credentials are not provisioned. Home Assistant remains dormant and performs no
private-network discovery.

## Isolation, consent and telemetry

Production reconciliation found:

- five active pilot tenants, Hermes profiles, approved WhatsApp identities and
  Bumpa connections;
- 26 tenant tables with row-level security enabled and forced;
- zero tenant tables missing the complete RLS boundary;
- five research-consent states reset to `pending`;
- zero research events after live synthetic canaries;
- content-free operational events continuing independently;
- no prompt, payload, message body or direct personal-data column in the
  operational telemetry table.

The implementation has independent kill switches for research, Sandbox,
managed images, multimodal/speech processing, external connectors, the primary
pilot and proactive messages. Provider failures preserve safe grounded Bumpa
answers and produce explicit unavailability rather than fabricated facts or
empty assistant responses.

## Guarded promotion, backup and stability

The root-owned promotion coordinator:

1. verified the immutable release boundary and environment contract;
2. created and verified the pre-promotion recovery point;
3. migrated from schema `0017` to `0018_agent_capability_audit`;
4. reconciled all five Hermes profiles and 19 tools per profile;
5. recreated the ten intended services;
6. passed direct-origin and public-edge smoke;
7. committed the release journal and cleared the maintenance interlock.

Final inspection found ten services running, all nine configured health checks
healthy, zero restarts, zero OOM kills, no severe recent log signature, active
UFW and verified Cloudflare/Docker firewall state. No private database, Redis,
API, web or Hermes service port is publicly listening.

The five-host edge smoke passed API health/readiness, apex content, canonical
`www`, admin/research authentication boundaries and two unique nonce-bearing CSP
documents. Readiness reports PostgreSQL, Redis, worker and scheduler healthy
with zero queued wakeups and the Meta/Bumpa/Hermes selectors.

The post-release guarded backup completed successfully. Its format-3 manifest
binds the exact application revision, schema `0018_agent_capability_audit`,
PostgreSQL 16.14 and the exact backup image above. PostgreSQL, exports, Hermes
runtime and Hermes staging are present, and all five recorded checksums replayed
successfully. The backup timer is active.

## Open activation gates

The production SME consultant core is deployed, but the full objective is not
complete until these external gates close:

1. Provision a Tavily secret and pass a current cited Ghana research canary.
2. Provision ElevenLabs Scribe/TTS credentials and an approved voice, then pass
   language/confidence, native voice-note and text-fallback canaries.
3. Provision selected Google and Meta Ads OAuth applications, connect synthetic
   tenant resources and pass read, preview, confirm, denial, expiry, revocation
   and idempotency canaries before enabling connectors.
4. Record each participant’s explicit research-consent choice. Product access
   must remain unchanged when consent is declined.
5. Obtain an approved internal/test WhatsApp recipient, then pass text, image,
   voice, document, video, progress, reply-context and delivery/read receipt
   canaries before messaging the five RCT participants.
6. Keep public onboarding, WhatsApp OTP and proactive delivery disabled until
   their independent approval gates pass.

Missing providers must remain fail-closed. Broad host-local tools are not an
acceptable substitute.
