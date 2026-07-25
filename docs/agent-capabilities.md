# Production SME consultant capabilities

Status: controlled five-business RCT pilot. Public onboarding and WhatsApp OTP remain disabled.

## Runtime boundaries

Each active Hermes profile receives a different MCP bearer credential. The credential resolves
to one tenant on the server; neither Claude nor a tool argument can choose a tenant ID. The MCP
endpoint is mounted at `/internal/mcp/`, is blocked by Caddy, and is reachable only on the private
application network.

Business tools return exact store-local date bounds, currency, freshness, inclusion rules and
coverage warnings. Customer output is aggregated. Current public research runs only through
Tavily and returns original HTTPS URLs as untrusted evidence. Search queries reject direct contact
details and customer, account or order identifiers.

Terminal, code, files and video-frame extraction run in the Cloudflare Sandbox Worker. A sandbox
ID is an HMAC of tenant and conversation workspace. Containers have no production mounts or
credentials, internet is disabled, commands and outputs are bounded, and inactive containers are
destroyed. Native Hermes terminal, browser, file, search, messaging, Home Assistant and image
generation toolsets remain disabled so they cannot bypass these boundaries. Approved image
generation runs through the Worker's Cloudflare Workers AI binding and is queued back to the
initiating tenant, user and conversation through the audited media path.

Connector reads require an active admin-approved connection, a server-side tool permission and a
resource allowlist. Writes can only be prepared. A pending action contains the exact target,
content and parameters; confirmation is tenant-bound, user-bound, expiring, single-use and
idempotent. Claude never receives an execute-write tool.

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
switch is on. Production environment validation fails closed when a corresponding provider secret
file or fixed Sandbox Worker HTTPS origin is missing.

`WHATSAPP_PRIMARY_PILOT_ENABLED` is the only exception to the legacy temporary web-PIN/primary
number interlock. It is valid only with the Meta primary sender enabled; it does not enable OTP,
public onboarding, proactive insights or unapproved templates.

Provider values are copied by `app-secrets-init` into a read-only runtime volume:

- `TAVILY_API_KEY_FILE_HOST`
- `ELEVENLABS_API_KEY_FILE_HOST`
- `SANDBOX_SERVICE_TOKEN_FILE_HOST`
- the existing Google and Meta OAuth client-secret files

Secret files must be absolute, non-symlink, one-line files with mode `0400` or `0600`. Never place
their values in `.env.production`, logs or release evidence.

## WhatsApp behaviour

The Bumpa Bestie webhook remains the Meta signature verifier and phone-to-tenant router. A signed
inbound message is durably claimed before acknowledgement. Read receipt and typing work starts as
an immediate background task. Processing is serialized with a renewable, privacy-hashed Redis
lock per WhatsApp user, and duplicate Meta message IDs remain idempotent.

Text, captions, replies, buttons, locations and contact cards are supported. Images reach Claude
vision. `WHATSAPP_SPEECH_ENABLED` separately controls ElevenLabs Scribe v2 transcription for
voice, audio and video; video also receives up to three
bounded Sandbox-generated frames. PDF and safe text documents are extracted without embedded
execution. Unsupported, corrupt, oversized and provider-failed media always produce an explicit
fallback.

The final response is chunked and quotes the inbound message. A requested voice reply is generated
as a native Ogg/Opus voice note, with the text response retained as fallback. After eight seconds,
at most two progress messages may be emitted, and only after real Hermes tool events. Delivery
state is monotonic (`sent`, `delivered`, `read`); failure evidence is separate.

On the first pilot conversation after this release, each RCT business receives an explicit
research-consent prompt. The migration resets previously granted tenants to `pending`. Declining
does not change product capability. Research events remain empty unless consent is granted;
content-free operational events continue.

## Release order and evidence

1. Provision Tavily, ElevenLabs, Google/Meta OAuth and Cloudflare Sandbox access.
2. Deploy the Sandbox Worker and set its `SANDBOX_SERVICE_TOKEN` secret.
3. Set the matching API host secret file and the Worker HTTPS origin.
4. Run migrations; verify schema head `0017_agent_capabilities` and forced tenant RLS.
5. Reconcile all five Hermes profiles and confirm their distinct MCP credential hashes.
6. Enable the main capability switch, then research, Sandbox, connectors and multimodal switches.
7. Canary one internal mapped user before enabling the five RCT users.
8. Verify text, image, voice, document, video, citations, exact periods, progress, confirmations,
   delivery/read status, provider-outage fallbacks and zero cross-tenant access.
9. Keep proactive templates off until the conversational pilot is stable. Keep OTP and public
   onboarding off.

Rollback is a switch change first. If a provider is unhealthy, disable only that capability and
leave grounded Bumpa reads active. Do not replace a failed managed provider with host-local tools.
