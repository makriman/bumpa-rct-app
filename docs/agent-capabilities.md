# Production SME consultant capabilities

Status: controlled five-business RCT pilot. Public onboarding and WhatsApp OTP remain disabled.

## Runtime boundaries

Each active Hermes profile receives a different MCP bearer credential. The credential resolves
to one tenant on the server; neither Claude nor a tool argument can choose a tenant ID. The MCP
endpoint is mounted at `/internal/mcp/`, is blocked by Caddy, and is reachable only on the private
application network.

Business tools return exact store-local date bounds, currency, freshness, inclusion rules and
coverage warnings. Customer output is aggregated. Public research uses the keyless open-source
DDGS client and returns original HTTPS URLs as untrusted evidence. Search queries reject direct
contact details and customer, account or order identifiers. Search failure is reported honestly;
it never permits an uncited external claim or blocks answers that can safely use store data alone.

Terminal, code, files, video-frame extraction, scanned-PDF page rendering and outbound-file
sanitization run in the Cloudflare Sandbox Worker. A sandbox ID is an HMAC of tenant and
conversation workspace. Containers have no production mounts or credentials, internet is
disabled, commands and outputs are bounded, and inactive containers are destroyed. Native Hermes
terminal, browser, file, search, messaging, Home Assistant and image-generation toolsets remain
disabled so they cannot bypass these boundaries. Approved image generation runs through the
Worker's Cloudflare Workers AI binding. Generated images, bounded MP4 video and sanitized
PDF/TXT/CSV/JSON documents are bound to the exact initiating message and delivered through the
audited web or WhatsApp media path; base64 content never enters the final assistant answer.

Connector reads require an active admin-approved connection, a server-side tool permission and a
resource allowlist. Writes can only be prepared. A pending action contains the exact target,
content and parameters; confirmation is tenant-bound, user-bound, expiring, single-use and
idempotent within the initiating request. A later user request receives a new confirmation
identity even when its proposed content is identical. Claude never receives an execute-write
tool.

Home Assistant is available only through the same curated connector control plane. It has no
private-network discovery and accepts only an operator-approved, tenant-supplied public HTTPS
origin plus an encrypted dedicated token. Reads are entity/service allowlisted; service calls
require both an allowlisted entity and service plus the ordinary exact-preview confirmation.
Until a tenant explicitly requests, receives approval for and connects an instance, the
capability is dormant.

## Capability switches

All switches default off and can be rolled back independently:

- `AGENT_CAPABILITIES_V2`
- `HERMES_TOOLS_ENABLED`
- `WEB_RESEARCH_ENABLED`
- `SANDBOX_TOOLS_ENABLED`
- `MANAGED_IMAGE_GENERATION_ENABLED`
- `EXTERNAL_CONNECTORS_ENABLED`
- `WHATSAPP_PRIMARY_PILOT_ENABLED`
- `WHATSAPP_MULTIMODAL_ENABLED`
- `WHATSAPP_SPEECH_ENABLED`
- `WHATSAPP_PROGRESS_ENABLED`
- `PROACTIVE_INSIGHTS_ENABLED`

`AGENT_CAPABILITIES_V2` requires Hermes. Dependent capabilities cannot be enabled unless the main
switch is on. Research requires the fixed `ddgs` provider and no paid-provider key. Speech requires
the Hermes-local runtime and a configured local-language allowlist. Production environment
validation fails closed when a required private Hermes/Sandbox boundary is missing.

`WHATSAPP_PRIMARY_PILOT_ENABLED` is the only exception to the legacy temporary web-PIN/primary
number interlock. It is valid only with the Meta primary sender enabled; it does not enable OTP,
public onboarding, proactive insights or unapproved templates.

Provider values copied by `app-secrets-init` into a read-only runtime volume are limited to:

- `SANDBOX_SERVICE_TOKEN_FILE_HOST`
- the dormant Google and Meta OAuth client-secret files when those coming-soon integrations are
  separately approved and enabled

Secret files must be absolute, non-symlink, one-line files with mode `0400` or `0600`. Never place
their values in `.env.production`, logs or release evidence.

Run the content-free capability report before and after each activation:

```bash
./scripts/agent_capability_status.sh .env.production | jq
```

The report never prints a secret, secret path, tenant coordinate or participant
identifier. It distinguishes `active`, `ready_disabled`,
`blocked_missing_prerequisite`, `misconfigured` and `disabled`. An operator must
still run the capability-specific canary: `active` proves configuration readiness,
not DDGS reachability, local model health or user consent.

## WhatsApp behaviour

The Bumpa Bestie webhook remains the Meta signature verifier and phone-to-tenant router. A signed
inbound message is durably claimed before acknowledgement. Read receipt and typing work starts as
an immediate background task. Processing is serialized with a renewable, privacy-hashed Redis
lock per WhatsApp user, and duplicate Meta message IDs remain idempotent.

Text, captions, replies, buttons, locations and contact cards are supported. Images reach Claude
vision. `WHATSAPP_SPEECH_ENABLED` separately controls local faster-whisper transcription for
voice, audio and video; video also receives up to three
bounded Sandbox-generated frames. PDF and safe text documents are extracted without embedded
execution. Scanned PDFs receive up to four bounded page images for Claude vision. A transcript
below the confidence threshold produces a deterministic quote-and-confirm response and cannot
run Hermes tools or actions. Unsupported, corrupt, oversized and provider-failed media always
produce an explicit fallback.

The final response is chunked and quotes the inbound message. Generated images, documents and
video use the corresponding native WhatsApp media types. A supported English/Pidgin voice reply
is generated locally with Piper as a native Ogg/Opus voice note, with the text response retained
as fallback. Unsupported TTS languages return text rather than a guessed voice. Typing is refreshed
while long work continues. After eight seconds, at most two progress messages may be emitted, and
only after real Hermes tool events. Delivery state is monotonic (`sent`, `delivered`, `read`);
failure evidence is separate. A Hermes outage produces a durable, honest control-plane response
instead of an empty response or dead letter.

All five RCT participants have supplied signed consent decisions outside the application. The
operator CLI records those decisions for the exact five-tenant list, policy version and tenant
owner, with a content-free `docusign` attestation marker. It does not accept or retain signed
documents, participant identities or document references. Recording is dry-run by default,
exact-count guarded and idempotent. Declining or withdrawing does not change product capability.
Research events remain empty unless consent is granted; content-free operational events continue.

## Release order and evidence

1. Build the pinned Hermes image and prove its local Whisper/Piper media loop.
2. Deploy the Sandbox Worker and set its `SANDBOX_SERVICE_TOKEN` secret.
3. Set the matching API host secret file and the Worker HTTPS origin; research itself needs no key.
4. Run migrations; verify schema head `0018_agent_capability_audit` and forced tenant RLS.
5. Reconcile all five Hermes profiles, confirm their distinct MCP credential hashes and verify
   all 19 managed tools before starting the public proxy.
6. Enable the main capability switch, then keyless research, Sandbox and multimodal switches.
   Keep Google/Meta OAuth connectors disabled and visibly marked coming soon.
7. Canary one internal mapped user before enabling the five RCT users.
8. Verify text, image, voice, document, video, citations, exact periods, progress, confirmations,
   delivery/read status, provider-outage fallbacks and zero cross-tenant access.
9. Keep proactive templates off until the conversational pilot is stable. Keep OTP and public
   onboarding off.

Rollback is a switch change first. If DDGS or a local model is unhealthy, disable only that
capability and leave grounded Bumpa reads active. Do not enable broad Hermes host tools or
unrestricted egress as a shortcut.
