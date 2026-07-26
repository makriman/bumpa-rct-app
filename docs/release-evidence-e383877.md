# Production SME consultant release evidence — e383877

This is the redacted operator record for application release
`e383877ce26dfd3881249508636f0a26c1becbd2`, promoted and verified on
2026-07-25/26 UTC for the controlled five-business RCT pilot.

This record contains no credentials, phone mappings, Meta message identifiers,
tenant/user/profile/session identifiers, participant content, hidden prompts,
raw provider payloads, business/customer/product labels, business totals, secret
paths, origin address or backup-directory identifier.

## Approved production decisions

The system owner approved implementation, privacy/security/retention work and
the controlled production deployment, with these explicit decisions:

- use keyless/open-source search rather than Tavily or another paid search API;
- use Hermes-local Faster-Whisper and Piper rather than ElevenLabs;
- keep Google and Meta Ads OAuth connectors marked `coming soon`;
- treat all five RCT consent records as received and signed outside the product;
- use one separately approved, mapped internal WhatsApp recipient for live
  outbound media and receipt canaries;
- defer encrypted off-host backup and an external alert destination for this
  controlled pilot; and
- keep public onboarding, WhatsApp OTP and proactive messages disabled.

The deferred durability and alerting controls remain required before a public or
unrestricted launch.

## Release chain and immutable images

- Free research, local speech and consent/governance work merged in
  [PR 73](https://github.com/makriman/bumpa-rct-app/pull/73) as
  `9ad0565d9c513add0ea15559e15f6ad485dc039f`.
- Scheduler capability isolation merged in
  [PR 74](https://github.com/makriman/bumpa-rct-app/pull/74) as
  `ef0073d03e71afedd722973b6466d10cb3d4192f`.
- Authoritative primary-source research routing and citation enforcement merged
  in [PR 75](https://github.com/makriman/bumpa-rct-app/pull/75) as the deployed
  application revision `e383877ce26dfd3881249508636f0a26c1becbd2`.
- Exact-main
  [CI 30179017277](https://github.com/makriman/bumpa-rct-app/actions/runs/30179017277)
  passed the full quality, browser, load, resilience, security and eight-image
  matrix.
- Exact-main
  [publication 30179563912](https://github.com/makriman/bumpa-rct-app/actions/runs/30179563912)
  published and scanned all eight immutable indexes. Their exact-index scans
  reported zero fixable high or critical vulnerabilities.

| Service              | Deployed OCI index reference                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------------------------- |
| API/worker/scheduler | `ghcr.io/makriman/bumpabestie-api@sha256:1a1446fbc145da95a56c970cade36e405e574b0da9c32a4b5558c2749d8611aa`          |
| Consumer web         | `ghcr.io/makriman/bumpabestie-web@sha256:9a1d92faaace187dd47b8602317ee9235957b37229f9e40fb6b7dcf9961b9e9b`          |
| Admin web            | `ghcr.io/makriman/bumpabestie-admin-web@sha256:b44fa33721c588da83cada77645fe9ae710d32d06034782537b434d8b4c829d4`    |
| Research web         | `ghcr.io/makriman/bumpabestie-research-web@sha256:5ba026909f2e5777699985792b2fe4b976b987028793b82a365bc4fc39cd0cee` |
| Caddy                | `ghcr.io/makriman/bumpabestie-caddy@sha256:53b65eb96fc57cca0fb9a626d2e00bcd13cb694fcfc89c878ae030135960fd88`        |
| PostgreSQL           | `ghcr.io/makriman/bumpabestie-postgres@sha256:67291b450b09cdf9485857857e08aae92b2b0621e10fc176703adeefcd547c17`     |
| Backup               | `ghcr.io/makriman/bumpabestie-backup@sha256:a5386746d6db9739cc4e180ee743206e0252bde527402c814538c0bf4f57540c`       |
| Hermes               | `ghcr.io/makriman/bumpabestie-hermes@sha256:2cf84616f73d23e4e2d8f8d4116042b3ef4f35123087230b7d6a821a60a103e7`       |

Each exact index carries the deployed revision label. The production checkout,
release journal, environment release reference and running image references
match the same revision and indexes.

## Quality and isolation

The final citation change passed locally with 592 API tests, one skip and 85.07%
branch coverage, plus Ruff formatting/lint, strict mypy, generated-contract
checks and a real keyless DDGS query constrained to official Ghana sources.
Focused citation/research coverage passed 59/59 tests.

The exact-main CI repeated the complete backend, frontend, browser, accessibility,
load, resilience, infrastructure, migration/RLS and image-security gates. The
adversarial suite covers:

- tenant selection attempts through prompts, MCP arguments, session keys,
  connector inputs, delegation and sandbox paths;
- private-network, credential, host-filesystem and unrestricted-egress attempts
  from terminal/code/document tools;
- exact preview, expiry, denial, user/tenant binding, single-use and idempotency
  rules for external writes;
- invalid signatures, unknown senders, opt-outs, duplicate webhook IDs and
  out-of-order delivery callbacks;
- corrupt, empty, unsupported and oversized media;
- provider outages and untrusted search/browser/document instructions; and
- research-content exclusion without consent while content-free operational
  telemetry continues.

The acceptance suite found no cross-tenant access path. The production scheduler
also runs with agent, research and speech capabilities disabled, so scheduled
maintenance cannot inherit tenant-facing tools accidentally.

## Grounded consultant and tenant data

The deployed assistant is the full SME consultant policy rather than the former
analytics snapshot summariser. It handles planning, marketing, sales,
operations, finance, expansion and general assistance while distinguishing:

- facts calculated from tenant-scoped Bumpa data;
- externally researched facts and original HTTPS citations; and
- assumptions, uncertainty and recommendations.

Supported calls are deterministic. Provider modes that control temperature omit
the incompatible parameter rather than failing the request. Business tool
results include exact inclusive dates, store timezone, currency, freshness,
inclusion rules and material coverage warnings.

All five active Hermes profiles are ready and expose 19 managed tools behind
separate encrypted tenant-bound credentials. The model cannot provide or select
a tenant ID. The tools cover profile and data coverage, overview/comparison,
sales and profit, orders, product mix, aggregated customer segments, supported
inventory, exact calculation/reconciliation, research, sandbox/media and safe
prepared actions.

Stable provider sessions and bounded conversation history are active on web and
WhatsApp. Quoted replies and explicit conversation reset are supported.

## Research and Ghana regression

Production research uses keyless DDGS through the managed Hermes research tool.
No Tavily credential or paid search dependency exists. Queries exclude private
store/customer data, retrieved content is untrusted, and write permissions
cannot be expanded by a webpage.

For regulated or market-entry questions, the research router prioritises
official primary-source domains and the response validator requires exact HTTPS
Markdown citations. A bounded one-pass repair may correct missing citation
formatting; if authoritative evidence remains unavailable, the answer fails
closed rather than inventing a source.

The authenticated production Ghana-expansion streaming canary passed:

- non-empty answer with facts, assumptions and recommendations;
- eight explicit dates, business currency, data freshness and tenant grounding;
- seven HTTPS citations, including three official Ghana sources;
- successful `research_web` plus six tenant business-data tools; and
- `tenant_data` and `external_citation` grounding flags in content-free
  operational telemetry.

Two sampled official URLs were independently reachable. The answer body and
business values were not copied into operator evidence.

## Exact periods and multi-turn memory

The production “last week” canary resolved in the store timezone to
`2026-07-13` through `2026-07-19` and compared it with
`2026-07-06` through `2026-07-12`. The answer contained the exact ranges,
currency and tenant grounding.

The follow-up “Yes, give me that template” reused the same conversation and
stable provider session and returned a substantive action-shaped template. Only
content hashes and structural booleans were retained in operator evidence.

## Local speech

Speech is provided inside the tenant-routed Hermes runtime:

- Faster-Whisper handles transcription with detected-language and confidence
  metadata;
- Piper provides supported English voice synthesis;
- TTS languages without a reliable installed voice return an honest text
  fallback; and
- the app receives no paid speech-provider credential.

A live production canary passed all five profile readiness checks, produced
non-empty Whisper text, detected English with high confidence, generated a
non-empty native Ogg/Opus voice note and denied a cross-profile media key. The
unsupported Yoruba TTS path returned the expected text fallback rather than a
guessed voice.

## WhatsApp live canary

The primary Cloud API sender remains behind Bumpa Bestie’s signature-verifying,
phone-to-tenant/profile router. The approved internal canary identity was
confirmed active, mapped, opted in and inside an open customer-service window
before any send.

The canary used the same idempotent delivery functions as normal agent replies.
It created durable, tenant/user-bound outbound rows and Meta provider IDs for:

- one explanatory text message;
- one PNG image;
- one PDF document;
- one short H.264 MP4 video; and
- one Hermes-local native Ogg/Opus voice note.

Text, image, PDF and voice reached monotonic `read` status. The finalized H.264
video reached `delivered`. All five successful rows have unique idempotency keys,
provider IDs and no delivery-failure marker.

An earlier synthetic fragmented-MP4 fixture was accepted for upload but later
received Meta error `131053` because Meta could not find an indexable video
stream. It remains recorded as a failed test fixture. It was not retried
ambiguously; a normally finalized H.264 MP4 was sent under a new idempotency key
and delivered successfully.

No new inbound media reply had arrived when this record was prepared. Therefore
the exact-release live claim is outbound text/image/document/video/voice plus
delivery/read receipts. Inbound image, voice, document and video processing is
implemented and covered by integration/adversarial tests, but its real-account
canary remains an external recipient-response gate and is not represented here
as complete.

Immediate read marking, typing refresh, bounded real-tool progress bubbles,
quoted replies, FIFO processing, duplicate suppression, media fallback and
monotonic status handling remain enabled for the controlled pilot. OTP and
proactive/template delivery remain disabled.

## Connectors and confirmation boundary

Google Drive, Sheets, Gmail, Calendar and Meta Ads remain `coming soon` because
their OAuth applications/credentials are intentionally not configured.

The connector registry, allowlists and write-confirmation control plane remain
deployed but inactive. Reads require an approved tenant connection. Every write
is prepared as an exact preview; confirmation is tenant/user-bound, expiring,
single-use and idempotent. The model has no direct execute-write tool.

Home Assistant support is dormant until a business explicitly connects a trusted
instance. Private-network discovery is prohibited.

## Consent, telemetry and retention

The five RCT participant consents were confirmed as signed and received.
Production consent records are `granted`; product functionality remains
independent of research participation.

Operational telemetry contains media type, tool, status, latency, error class,
citation count and grounding flags, without message bodies or direct customer
data. It remains separate from consent-gated RCT content. Provider credentials,
raw customer records and hidden prompts are excluded from logs and research
events.

Independent kill switches cover research, sandbox tools, multimodal/speech,
external connectors, the primary pilot and proactive messages. Raw downloaded
media is bounded, encrypted in temporary storage and removed after processing
or within the retention limit.

## Guarded promotion, backup and stability

The root-owned promotion coordinator:

1. verified the old release, free-space threshold and immutable target boundary;
2. installed and byte-verified the exact coordinator helpers;
3. pulled all eight exact indexes and verified their revision labels;
4. stopped the old writer set without overlap and created a checksummed recovery
   point;
5. ran the migration gate at schema `0018_agent_capability_audit`;
6. reconciled five Hermes profiles and 19 required tools per profile;
7. recreated all ten intended services and passed direct-origin and public-edge
   smoke; and
8. committed the exact release journal and cleared the promotion interlock.

After the live provider canaries, the guarded scheduled-backup workflow
quiesced API, worker, scheduler and Hermes, produced a new format-3 local
recovery point and resumed exactly those services. Its six-file inventory and
five SHA-256 entries replayed successfully. The manifest binds:

- application revision `e383877ce26dfd3881249508636f0a26c1becbd2`;
- schema `0018_agent_capability_audit`;
- PostgreSQL 16.14; and
- the exact backup image listed above.

The local backup timer is enabled and active. Final public-edge smoke passed and
all ten services were running healthy with zero restart counts.

Encrypted off-host durability, a remote restore and signed external alert
receipt are intentionally not claimed. They are accepted pilot risks, not
completed controls.

## Controlled-pilot conclusion

The production objective is met for the controlled five-business RCT pilot:
grounded SME consulting, tenant data, stable context, authoritative research,
local speech, isolated tools, audited actions, responsive multimodal WhatsApp
and independent safety controls are live on the exact immutable release.

The remaining boundaries are explicit:

1. wait for the approved recipient to send real inbound voice/image/document/
   video replies before marking the exact-release inbound-media canary complete;
2. keep Google and Meta Ads OAuth capabilities `coming soon`;
3. keep public onboarding, OTP and proactive messages disabled; and
4. add encrypted off-host backup, an external alert destination and a remote
   restore before any unrestricted launch.
