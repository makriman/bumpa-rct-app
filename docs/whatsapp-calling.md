# Calling Bestie on WhatsApp

Inbound calling is implemented as an optional voice service. It uses the existing
primary Meta business number, Hermes business tools, local Whisper transcription,
and Piper English speech. A linked, approved user calls the business number from
WhatsApp; Bestie answers and saves the text turns in a separate WhatsApp
conversation in that user's account. Raw call audio and SDP are not stored.

The feature is **disabled by default**. Local WebRTC tests are not evidence that
Meta calling has been enabled or that a production phone call works.

## Architecture and boundaries

1. The existing `/webhooks/whatsapp` endpoint verifies Meta's HMAC signature.
2. Call events are forwarded unchanged, with the signature, to the private
   `voice:8001/webhooks/whatsapp/calls` endpoint, which verifies it again. Only the
   configured primary WABA/phone number is accepted. Message changes continue
   through the existing durable inbox; call SDP never enters that inbox.
3. The gateway authorizes the caller against approved phone identity, opt-out,
   active user, active tenant membership, active tenant, and active Hermes profile.
   The initial phone lookup is privileged. Business queries and chat writes use
   the tenant's PostgreSQL RLS context. Access is rechecked on each speech turn.
4. An aiortc peer answers the SDP offer with Opus over DTLS-SRTP. Meta receives
   `pre_accept` and `accept` actions with the generated SDP answer. The outgoing
   audio track stays paused until `accept` succeeds; a failed or interrupted
   acceptance cannot send a greeting or silent audio frames.
5. WebRTC VAD detects speech and pauses. Each bounded utterance goes to local
   Whisper, then the existing `handle_chat` service, then Piper. Failed
   transcription, uncertain detected language, or a detected language outside
   the supported English path gets a spoken request to repeat without invoking
   chat/tools. Language probability measures language detection, not word
   accuracy; names, amounts, Nigerian English and Pidgin still need evaluation. Hermes retains
   its business context, connector permissions, and action confirmation flow.
6. Speech interrupts pending playback. Obsolete responses are discarded; an
   already-running Hermes/tool request is allowed to finish and can still appear
   in the transcript. Interruption does not undo an action. Approvals must be
   reviewed in the app; saying “yes” on a call does not bypass that flow.
7. Hangup, media failure, shutdown, or the duration limit closes the peer. Redis
   tombstones prevent duplicate/reordered webhooks and restart replays from
   starting a second assistant. Reconnecting after a gateway restart requires a
   new user call.

This version is **utterance-based**, not streaming speech-to-speech. It waits for
700ms of silence by default, and transcription, reasoning, and synthesis add
latency. English speech only; the service speaks a short AI/transcript disclosure
when answering. There is one active call per caller and four globally by default.
The turn buffer is at most 20 seconds, audio playback at most 60 seconds per
answer, and a call at most 10 minutes. An interrupted worker keeps its capacity
slot until its synchronous provider request drains. There are no outbound calls,
PSTN forwarding, attachment delivery, or raw audio recordings. Ask for generated
files/images in text chat. Active call conversations use status `voice`, so the
text router cannot select them; normal hangup marks them `closed`.

## Deploy and activate

Use a **single voice replica with one Uvicorn worker**. Peers are process-local;
multiple replicas require explicit ownership/routing and are not supported here.
The main API can keep its existing worker count. Use Redis persistence and a
no-eviction policy for call tombstones. The existing Redis deployment persists
state; do not flush the Redis database while calls are running.

1. Confirm calling eligibility for the actual Meta business account and primary
   phone number in WhatsApp Manager. Calling availability is distinct from
   messaging availability. A Meta test/verification sender is not supported by
   this gateway. Subscribe the existing app webhook to the `calls` field while
   retaining `messages` and the current callback URL/verify token.
2. Provision an authenticated self-hosted TURN relay reachable from the voice
   container, for example coturn on the same Droplet or existing authorized
   infrastructure. The current product constraint excludes a new hosted speech
   or relay provider. Validate public ICE reachability and relay resource use
   before activation; this guide does not provision or open relay ports. Set, for example, `WHATSAPP_CALL_ICE_URLS=turn:relay.example.com:3478`
   plus `WHATSAPP_CALL_TURN_USERNAME` and `WHATSAPP_CALL_TURN_CREDENTIAL` in the
   protected production environment file. TURN credentials must not be committed.
   Production startup rejects missing TURN configuration. No voice container
   ports are published: HTTP stays behind the API/Caddy and media uses outbound
   ICE/TURN. Permit the relay's chosen UDP/TCP egress in host/network rules.
3. Publish the `voice` image using the release-image workflow (or build
   `apps/api/Dockerfile.voice`) from the same revision as the API. Set `VOICE_IMAGE`
   to its immutable registry digest. The voice image uses Debian because the
   media libraries publish manylinux wheels; the main API stays on Alpine.
4. Set `WHATSAPP_CALLING_ENABLED=true`, `WHATSAPP_BACKEND=meta`,
   `AGENT_BACKEND=hermes`, and `WHATSAPP_SPEECH_ENABLED=true`. Ensure the primary
   sender is enabled and its WABA/phone IDs and existing Meta secrets are set.
   Ensure Hermes local speech is provisioned and healthy.
5. With the application already deployed at this revision, start the optional
   service and recreate the API so it reads the calling flag:

   ```sh
   docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml \
     --profile voice pull voice
   docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml \
     --profile voice up -d --no-deps voice api
   docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml \
     --profile voice exec -T voice python -c \
     "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health/ready').status)"
   ```

   The health check verifies gateway/Redis availability, not Meta eligibility,
   TURN connectivity, or speech quality. This opt-in service has its own rollout;
   the existing `scripts/deploy.sh` does not manage its image/rollback. Stop and
   drain `voice` before a normal application deployment/migration, then update
   its matching image and restart it afterward. Do not leave an old voice writer
   running across database migrations. The promotion script refuses to proceed
   while voice is running. Scheduled backups drain and restart an active voice
   service along with the other application writers.
6. Enable calling in Meta's phone-number settings with Graph signaling, **SIP
   disabled**, call status `ENABLED`, and the call icon visible. Do this only after
   the gateway passes its checks. Keep outbound/callback calling disabled for this
   inbound-only release. Meta's settings operations and permissions must be
   checked against the current API version before making the change.
7. From an approved user's WhatsApp, call the primary number. Verify two-way audio,
   the greeting, a tenant-scoped business question, an interruption, hangup, and
   the saved conversation. Verify an unknown number is rejected. Record the
   actual revision, image digest, and redacted results in the verification ledger.

For rollback, disable calling in Meta first, set `WHATSAPP_CALLING_ENABLED=false`
and recreate the API, then stop the voice service with its configured grace
period. Existing messaging remains available. Do not simply kill a gateway that
is still processing a tool request.

## Local verification

```sh
cd apps/api
uv sync --all-extras --locked
uv run pytest tests/test_whatsapp_calling.py tests/test_voice_media.py
uv run mypy app
```

Tests cover signed webhook routing, primary sender scoping, authorization and
revocation, tenant isolation, transcript persistence, signaling payloads, duplicate
and reordered events, Redis failure, VAD limits, interruption, hangup during
setup, and actual loopback WebRTC audio transport. The PostgreSQL CI RLS script
also exercises voice authorization with a role that cannot bypass RLS.

Protocol references: [Meta Calling API](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/),
[360dialog inbound call signaling](https://docs.360dialog.com/docs/messaging/calling/inbound-calls/how-to-handle-inbound-calls),
and [aiortc API](https://aiortc.readthedocs.io/en/latest/api.html).

## October 27 local safety review

The review candidate adds language/failure clarification and gates outgoing media
until successful acceptance. No live audio quality or latency is established by
these local tests. A declared four-call capacity is not a measured operating
capacity: start a future controlled pilot at one call and increase only after
measuring STT queue wait, transcription real-time factor, Claude first-result
latency, Piper first audio and CPU/RAM/swap. Whisper is CPU int8 with two threads
and one worker; STT and TTS each have process-wide locks in the current control
service. Keep voice notes and live calls as separate acceptance paths.

A returning call gets its own private call conversation. Generated transcript
text is not proof that every word was played or heard; track interrupted playback
separately before using spoken-output history as authoritative user-visible
context. Caller identity authorization is rechecked after transcription and before
speech. An utterance cannot bypass the app's exact action confirmation flow.
