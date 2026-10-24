# Onboarding and WhatsApp acceptance review

This is the review candidate in draft [PR 116](https://github.com/makriman/bumpa-rct-app/pull/116).
Status at record creation: do not deploy from this workspace. Exact-commit CI and
exact-head image checks are pending; this checkpoint does not grant release acceptance. The final handoff must
record the candidate SHA and its CI run separately from this self-referencing source.

## Provenance and goals

Both requested local threads were read through the supported Codex app API and
finished before final review:

- `01a16be7-bfb2-7b10-9ef4-e9f67d961802`, **Read Google Doc**: draft
  [PR 117](https://github.com/makriman/bumpa-rct-app/pull/117), final source
  `73378ad563e9c3f0fe78e660113cf60da913c1b7`.
- `01a16be8-1de6-7af2-ad59-02b81fe85d4c`, **Format concise WhatsApp replies**:
  draft [PR 115](https://github.com/makriman/bumpa-rct-app/pull/115), source
  `66fed69a5a086f85b4268b23e39ff1171d4d0c7d`.

Canonical repository: `makriman/bumpa-rct-app`. Both changes are integrated in the
review branch `codex/onboarding-acceptance-review`. The dirty primary checkout at
`codex/fix-live-sync-assistant` / `defab39` remains untouched. Its unrelated voice
work is excluded. Necessary security changes were reviewed selectively and tested.
Local memory storage was empty and Personal Context unavailable. Historical reports
about Nigerian number/login problems are not current integration evidence.

The [source onboarding document](https://docs.google.com/document/d/1PWA6WIh0sZbNHmrmx1wEA__2Efrlff4uBpFTazrzxPI/edit)
was read directly: seven business questions, review/correction, useful first advice,
persistent context and explicitly confirmed later updates. Existing controlled
pilot enrollment and separate research consent are preserved.

The [Research ICT Africa Nigeria survey](https://researchictafrica.net/research/digital-technology-adoption-in-microenterprises-nigeria-report/)
covers 718 microenterprises and varied digital access/use. The design inference is
optional resumable setup, mobile controls, uncertain/seasonal orders, service and
offline businesses, and plain language. No city, gender or Pidgin preference is
assumed. These tests are not merchant interviews or evidence of nationwide usability.

FastAPI owns authorization, tenant boundaries, reviewed facts and confirmations.
Hermes remains the agent harness with a private profile per business and tenant-bound
MCP tools. Its [native memory snapshot](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
is frozen at session start, so reviewed current business facts are supplied on each
request, including continued conversations and businesses without Bumpa data.
Draft facts never silently become reviewed memory. Old native memory yields to the
current reviewed profile. Provider secrets remain server-side.

## Review improvements

- Prevent a late WhatsApp progress bubble when final completion happens during a
  slow provider read/typing call. Preserve existing retry deduplication and the
  per-run limit; a send already in flight cannot be recalled.
- Commit deterministic onboarding input, state and reply atomically. A simulated
  failure before the outbound event now rolls back the answer and permits retry.
  The model/tool path still commits its initiating message before MCP use.
- Route recognizable English/Pidgin business help requests out of active setup,
  including unpunctuated requests, instead of saving them as profile facts.
- Route non-text attachments to the agent while pausing setup and preserving the
  existing draft; generated media prompts never become profile answers. Preserve
  transcribed audio/voice-note answers and ignore setup commands in attachment captions.
- Put useful next questions before an expandable saved-details review. Browser
  tests require the first realistic question to fit fully in the mobile viewport.
- Load the reviewed profile when an existing conversation has no messages; the
  route matrix now requires its actual heading instead of accepting a permanent
  loading placeholder.
- Give existing authenticated E2E fixtures a valid reviewed profile endpoint;
  otherwise screenshot tests accidentally exercised a missing-route error panel.
- Repair release-blocking dependency locks, Caddy certificates and pinned runtime
  images without relaxing tests, vulnerability thresholds or audit exclusions.

## Dependency and runtime compatibility

Node 22.23.4, uv 0.13.5, Next 15.5.28, PostgreSQL 16.15, Caddy 2.11.7 and the
reviewed Python patch updates are pinned. Sandbox remains on the stable 0.x SDK,
0.12.14, with its matching immutable image; this is not a migration to SDK 1.0.
Generated Worker bindings and the Docker build context match the updated tooling.

The Hermes immutable upstream payload records 0.22.5 / commit
`bf0203a794a07aac3ff764972ab9087baa4e4f4e`. Its bootstrap distribution metadata
incorrectly says 0.0.0. A fail-closed build step verifies that exact clean CI stamp,
exercises its WebSocket Host/Origin rejection, and reconciles package metadata to
the actual release. No advisory is ignored. Tests reject unknown/dirty releases,
conflicting metadata and different wheel layouts. Brotlicffi is patched separately.

New Hermes gateways default to multiplexing; this application still uses one API
listener per tenant profile. Generated configuration and runtime fixtures explicitly
set `gateway.standalone: true`. Before activating the image, reconcile existing
profile configs through the normal application control plane. Preserve all profile
credentials and memory. The runtime contract proves profile activation and restart.
An upstream PM warning about pruned npm tooling is still emitted; the derived image
has its own locked agent-browser runtime and passes gateway contracts. Do not run
upstream self-update inside this externally managed image.

Sandbox's npm has patched undici/ip-address. Current pip 26.2.2 still vendors
vulnerable dependencies. Its [documented complete debundling method](https://pip.pypa.io/en/stable/development/vendoring-policy/#debundling)
uses the complete hashed wheel set in `infra/sandbox/pip-dependencies.txt`, isolated
from global packages. Image build checks pip listing, dependency health, HTTPS wheel
download, offline install and import. The full wheel dependency audit is in CI.
API runtime pip/ensurepip are removed; build-stage package installation is retained.

## Reproducible evidence

Local commands and evidence are retained in this independent checkout. Raw scanner
reports live in ignored `.evidence/security`; checked-in
`artifacts/review-security/image-summary.json` records image identities, counts and
raw report hashes. Local image tags are evidence, not publishable release references.

| Gate | Current local evidence |
| --- | --- |
| `make quality` with pinned Node/locked Python | Passed; API 641 passed, 1 skipped, 85.51% branch coverage; web/admin/research 120/34/18 unit tests; lint/format/mypy/contracts/builds/isolation/Lighthouse |
| Operational unit tests after image changes | 113 passed |
| Sandbox typecheck and tenant/filesystem boundary tests | Passed, 3 tests |
| PostgreSQL migration 18→19 and non-bypass RLS | Passed, 27 tenant tables |
| `apps/api/scripts/verify_business_onboarding.py` | Passed against disposable PostgreSQL using a non-superuser, non-bypass application role |
| Actual HTTP/Postgres scenarios | Mock OTP login, seven answers, multi-channel Other, uncertain orders, explicit save, duplicate replay, crash rollback/retry, competing revisions (200/409), member visibility/write denial, cross-tenant denial, unchanged research consent |
| Frozen production Python dependency audit | 59 packages, zero known findings |
| Complete Sandbox pip wheel audit | Zero known findings |
| Full npm audits: consumer/admin/research/Sandbox/Hermes | Zero findings |
| API/Caddy/Postgres/backup/Hermes/Sandbox image scans | Zero fixable critical/high findings, unchanged CI policy |
| Final combined infrastructure contract | Passed activation/restart, patch upgrade, persistence, checksum backup and isolated restore |
| Pinned Linux Playwright | Strict consumer 78 passed, 4 viewport-specific skips; admin 34 passed; research 28 passed. Changed Linux screenshots manually inspected; thresholds unchanged |

An initial overlay-only pip-audit invocation inspected zero packages and was
discarded; only the exported frozen lock audit above is evidence. A final
infrastructure rerun was invalidated by editing the executing shell script and
was discarded; the stable script is being rerun. macOS has no shellcheck/flock
and cannot prove the Linux root-bind contract; exact-SHA Linux CI must cover them.
Changed chat/profile states and mobile browser text rendering were inspected.
The reviewed image hashes are recorded in `artifacts/review-security/visual-summary.json`.
Screenshot thresholds are unchanged. Sending/error fixtures now use a fixed clock.
Strict comparisons must pass before release acceptance.

Run the HTTP/Postgres gate from `apps/api` after migrations using `PYTHONPATH=.`,
`APP_ENV=test`, `AUTH_LOGIN_MODE=whatsapp_otp`, `SEED_DEMO_DATA=false`,
mock agent/WhatsApp/Bumpa backends,
loopback `DATABASE_URL` for a restricted application role and disposable Redis.
Then run `uv run --locked python scripts/verify_business_onboarding.py`.
The script rejects superuser/bypass roles and remote/production configurations.

## Fresh release handoff and rollback

1. Use a fresh clean checkout of the final accepted SHA. Verify exact-SHA CI,
   required review gates, current production baseline and image revision labels.
   Compare against the actual deployed commit/features, including any separate
   voice or security patches; stop if this candidate would remove deployed work.
   Run locked bootstrap/quality, pinned Linux E2E, actual non-bypass PostgreSQL
   integration and migration compatibility. Never deploy this review checkout.
2. Publish immutable images for that SHA and retain digests and security reports.
   The current release's separately applied security baseline must not be lost.
   Verify auth expiry, provider availability and existing profile config compatibility.
3. Follow `docs/deployment.md` / `docs/runbook.md` using the installed stable
   promotion coordinator. Quiesce writers and take a verified recovery point.
   Migration `0019_business_onboarding` is additive; apply before the new API.
4. Canary interrupted/repeated onboarding, corrected facts in continued and new
   sessions, role/tenant denial and provider failures. Perform an authorized live
   Hermes/Meta canary to verify actual recall, wording, Nigerian-number eligibility
   and delivery. Local mock OTP/transport tests do not establish those facts.
5. Roll back with the coordinator's prior app/auth image boundary while retaining
   compatible forward schema and patched infrastructure. Do not drop reviewed
   business memory as an outage rollback. Use verified restore only through the
   documented recovery procedure if compatibility cannot be maintained.

Outstanding at record creation: final replacement-SHA CI and image matrix, real provider canaries, and merchant/real-network usability. No production
state, real participant conversation or deployment has been accessed or changed.
