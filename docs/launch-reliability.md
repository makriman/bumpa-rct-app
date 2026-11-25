# Chat readiness and partial Bumpa data

Chat readiness is independent of complete analytics coverage. An operator may
explicitly accept a degraded initial import when it contains independently
validated analytics or a verified order publication. Failed imports with no
usable facts, a changed provider boundary, or an unverified job remain blocked.
The decision is persisted and audited; the import keeps its real degraded status
and missing figures remain unavailable. Approved identities awaiting setup receive
a deterministic setup notice. Suspended, archived, disabled and opted-out accounts
retain their existing denial rules.

## Currency-aware orders

Version 2 order publications retain each normalized order's original currency
and stamp that currency into server-owned provenance. Legacy version 1 records
are still readable under their original base-currency restrictions. Complete
pulls reconcile membership across currencies without deleting retained evidence.
Undated, duplicated, out-of-period or invalid-currency orders fail closed with a
typed diagnostic in `_bumpa_order_publication.reason`.

Financial tools default to the store currency. `get_business_overview`,
`get_sales_trend`, `get_order_breakdown` and `get_product_performance` also accept
`currency_code`. Coverage and business-overview responses expose separate totals
for each published currency. Amounts are never added across currencies or
implicitly converted. Existing proactive message templates retain their stricter
single-currency boundary.

## Reserved WhatsApp processing

PostgreSQL remains the source of truth and keeps existing job IDs, idempotency
keys and claim epochs. The outbox routes `whatsapp.*` wakeups to the Redis
`<queue-key>:whatsapp` list. The worker runs one bulk consumer and two reserved
WhatsApp consumers by default. `ASYNC_WHATSAPP_WORKERS` accepts 1 through 8;
changing it does not increase Bumpa request concurrency.

Each consumer owns its database sessions and lease renewal. Receiver/actor FIFO
and claim fencing still control admission. On startup, queued legacy WhatsApp
IDs are republished to the reserved list; duplicate old wakeups cannot repeat a
completed effect. Both lane heartbeats are required for worker health.
`/health/ready` additionally reports `whatsapp_queued_wakeups`,
`whatsapp_oldest_wait_seconds` and `whatsapp_queue` (`delayed` above 60 seconds).
Readiness counts alone must not be treated as a reply-latency guarantee.

## Product overview timeouts

Bumpa product overview has a bounded 90-second read window and at most two
attempts. A provider HTTP 504 is recorded as an upstream failure; increasing the
client timeout cannot repair a gateway that has already returned 504. Other
validated datasets and orders remain available, and the normal scheduled import
retries the failed dataset. This failure no longer occupies a WhatsApp consumer
or prevents an explicitly accepted partial-data account from activating.

Do not substitute another period, invent an inventory value, relabel foreign
currency, or turn unavailable data into zero. Escalate persistent provider 504s
with the endpoint, provider scope, requested local dates and measured latency.

## Evidence and rollout

Known operational date fields retain strictly validated ISO dates/timestamps
in redacted provider evidence. Customer/contact fields and arbitrary strings
remain redacted. Migration `0022_partial_data_onboarding` adds only the explicit
partial-data acceptance flag, defaulting existing accounts to false.

Release verification must cover native-currency totals, legacy publication
exclusion, a WhatsApp handler completing while a bulk handler is blocked,
old-queue recovery, explicit partial-data acceptance through final activation,
and unchanged suspension/opt-out rules. After promotion, pull fresh provider
data through durable jobs and reconcile per-currency canonical counts with the
provider's complete pagination. Any remaining upstream dataset error must stay
visible in the release evidence.
