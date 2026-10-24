# Merchant onboarding and business memory

An owner-authorized existing-pilot rollout can arm the next WhatsApp inbound for
an explicitly captured set of approved identity IDs and keyed phone fingerprints,
including secondary numbers. This is an operator-only migration-lane command,
not public enrollment or a proactive campaign. New or paused setup opens its
pending question; active setup accepts the next answer. Greetings repeat the
pending question without becoming a business fact. Completed setup is preserved.
The marker is consumed transactionally with chat state, so replay cannot restart
setup. Media/voice handling, immediate help, LATER, opt-outs, membership/tenant
authorization and web continuity remain in force. Future identities receive no
default merely because they share a user or tenant with the captured pilot.

Source: [Bumpa Bestie Onboarding](https://docs.google.com/document/d/1PWA6WIh0sZbNHmrmx1wEA__2Efrlff4uBpFTazrzxPI/edit).

## Comparison with the previous app

| Document requirement             | Previous app                                        | Implemented behavior                                                                                                      |
| -------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Introduction and start           | General chat empty state; admin provisioning wizard | Merchant welcome inside chat; separate from controlled account enrollment                                                 |
| Seven questions, one at a time   | Tenant category/location only                       | Products/services, customers, all selling channels, monthly orders, people including owner, priority, free-text challenge |
| Flexible selling channels        | No merchant preference collection                   | Every document option, multiple selections, required description for Other                                                |
| Use existing reliable Bumpa data | Metrics available to agent, no onboarding reuse     | Optional dated order-range suggestion from a complete prior calendar month and active connection boundary                 |
| Summarize what was learned       | No merchant review                                  | Review all seven answers, change any answer, explicitly save                                                              |
| Persistent structured profile    | Identity and synced metrics only                    | Application-owned, tenant-isolated reviewed business memory; drafts stored separately                                     |
| Transition to useful advice      | Generic sales/stock prompts                         | Goal-based first question plus the document’s naira pricing example; free chat remains available                          |
| Update as business changes       | No structured merchant updates                      | Profile editor and Hermes proposal tool; exact preview, explicit confirmation, expiry and supersession                    |

## Audience decisions

These are design judgments to validate with Nigerian merchants, not claims that all SMEs have the same needs. Use plain Nigerian English, accept Pidgin without forcing it, include products and services, and support physical shops and market stalls alongside social selling. Count the owner, part-time staff and regular family help rather than asking only about formal employees. Allow uncertain or seasonal order volume. Ask what matters now without requiring a budget, registration document, revenue figure, business jargon or another app connection.

[PwC’s Nigerian MSME Survey 2024](https://www.pwc.com/ng/en/assets/pdf/2024-pwc-msme-survey-report.pdf) reports broad smartphone use among its respondents and varied adoption of more specialized technology (p.47). This supports a mobile-first interface with simple controls; it does not establish that every merchant has a smartphone, stable connectivity, or the same digital skills. Saved progress, retryable errors, numbered WhatsApp replies and an optional setup path address interruptions without claiming to provide offline operation.

## State and permissions

`merchant_business_profiles` stores one JSON state per tenant, protected by enabled and forced PostgreSQL RLS. Existing account approval, WhatsApp phone authorization and enrollment remain prerequisites. This does not create public self-registration.

Owners/admins can edit. Members can read reviewed business facts but cannot read drafts or another user’s pending update. Drafts do not become assistant context until the merchant confirms the full review. Saving a profile does not grant or alter research consent. Audit records contain action, field names and revision, not answer text.

Every mutation locks the tenant row and compares the submitted revision. A stale web screen or WhatsApp action cannot silently overwrite another channel’s changes. Continue saves one answer; Finish later preserves the draft; Resume finds the next unanswered question. Direct business questions and setup can coexist. Recognizable questions interrupt active text onboarding instead of being saved as profile facts. Voice calls do not enter the numbered text questionnaire.

## Bumpa and Hermes

An order hint requires a completed, usable sync for exactly the prior full calendar month, with an available order count and an active connection whose boundary matches the sync. Missing or incomplete data stays unknown. The hint is dated and optional: Bumpa sales may exclude other selling channels, and a calendar month may not be typical. Choosing it explicitly records the merchant’s usual range; it does not turn that range into a verified current-period metric.

[Hermes persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) is bounded and loaded as a frozen snapshot at session start. Reviewed business facts therefore live in the application database and are included in every business-context request, including when Bumpa has not synced. `get_business_profile` also returns reviewed facts and allowed profile choices. Current reviewed facts take precedence over older session memory.

`propose_business_profile_update` requires a signed, unexpired action context, an actual initiating inbound message, matching tenant/user/conversation/channel, and a current owner/admin membership. It validates the proposed facts and creates an expiring preview. It cannot directly replace confirmed facts. Chat confirmation includes a fresh short code and must come from the same user, conversation and channel. New proposals supersede old codes; editing invalidates pending proposals. The profile page can confirm the current actor’s visible preview using the current revision. Ambiguous statements such as “I hired two people” require establishing the new total before choosing a team-size band.

## Verification and rollout

The migration is additive (`0019_business_onboarding`); the old application can continue without using the new table. Apply it before rolling out the API. The generated OpenAPI and TypeScript contracts include the business-profile endpoints. The existing PostgreSQL RLS verification gate now seeds profiles for both tenants.

Local tests cover review versus draft memory, channel revisions, resume, choice validation, uncertain orders, number-based WhatsApp setup, interruptions, actor visibility, exact confirmation, supersession/expiry, and complete-month order evidence. Browser tests cover desktop/mobile completion, corrections, saved progress after reload, failed-save recovery, accessibility, and screenshots with synthetic merchants.

Live WhatsApp transport and live Hermes extraction/recall still require a staging exercise. Mock/local tests do not prove external delivery or live model behavior. Usability with merchants and real network interruptions remains a pilot validation task.

The WhatsApp setup also accepts explicit Pidgin start/pause/resume commands,
natural `WhatsApp and Physical shop` channel choices, uncertain-volume answers
and `na only me` team answers. These become drafts until review/save. Review
lines are numbered to match EDIT 1–7. Completion gives one practical first step
for the selected priority and one follow-up, while LATER offers immediate help
without requiring setup. Existing media interruptions, transcribed voice-note
answers, corrections and per-turn reviewed memory remain shared with web.
