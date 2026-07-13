# Privacy and Retention Policy

Policy version: `2026-07-13-draft-1`

Status: **draft — not approved for unrestricted production use**

System owner: Bumpa Bestie
Required approvers: named privacy reviewer and named security/operations reviewer

This document is the reviewable governance boundary for the Bumpa Bestie pilot.
It describes controls that exist in code separately from decisions that still need
human approval. It does not turn an unresolved retention decision into an implied
permission to keep data indefinitely.

## Purpose and scope

Bumpa Bestie processes account identity, Bumpa commerce data, SME questions and
assistant responses so authorized members can understand their business. It also
supports consented, permissioned product research and the minimum operational
evidence needed to secure, debug and recover the service.

Data may be used only for:

- authentication, tenant administration and requested product functionality;
- generating business answers from the requesting tenant's current data;
- consented product research through the dedicated researcher boundary; and
- security, reliability, incident response and legally required records.

It must not be used for unrelated advertising, cross-tenant profiling, sale of
personal data or model training outside a separately approved consent purpose.

## Enforced controls

- Tenant authorization is enforced in the application and by forced PostgreSQL
  row-level security. Research pseudonyms use a purpose-specific configuration
  domain decoupled from provider-credential encryption. The first rollout preserves
  existing pseudonyms by initializing it from the former shared material; a later
  reviewed migration can separate the key material without coupling future field
  encryption rotation to pseudonymous identifiers.
- Raw research access is restricted to an authorized platform administrator,
  requires a bounded written reason, is audited and returns non-cacheable content.
- Audit request metadata never stores a full network address: IPv4 is reduced to
  its `/24` network and IPv6 to `/48`. User-Agent storage uses a fixed
  client-major/platform taxonomy instead of caller-controlled text.
- Research collection stops after consent withdrawal. Existing downloadable
  artifacts are invalidated, every download is reauthorized, and generated report
  packages expire after 24 hours.
- Provider credentials use authenticated field encryption. The first dual-read
  rollout keeps writing the predecessor-compatible v1 envelope; v2 writes and
  re-encryption require an explicit post-soak promotion after the rollback floor
  supports both versions.
- Audit logs expire after 365 days and sanitized system-error records after 90 days
  by default. Daily, bounded jobs enqueue bounded continuations until the expired
  backlog is below one batch. The system-error window cannot be shorter than the
  operational-alert discovery window.
- Logs, prompts, research exports and alert envelopes apply structured redaction;
  credentials, OTPs, authorization headers, cookies and raw provider payloads are
  forbidden in operational output.

## Retention schedule and unresolved decisions

| Data class | Current technical behavior | Proposed maximum | Approval state |
| --- | --- | ---: | --- |
| Generated research report/export artifacts | Authenticated private storage; checksum verified; withdrawal invalidates; cleanup job | 24 hours | Enforced; approve purpose/access |
| Audit logs | Reason/action/resource evidence plus privacy-bounded request context; draining cleanup | 365 days | Enforced default; reviewer approval required |
| Sanitized system errors | Bounded operational metadata; draining cleanup | 90 days | Enforced default; reviewer approval required |
| OTP and browser session material | Hashed OTP, expiry, one-time use, revocation and session expiry | Product-configured expiry only | Enforced |
| Bumpa raw responses, metric snapshots, canonical orders/items | Tenant-scoped and access-controlled; no automatic deletion window yet | **Decision required before unrestricted traffic** | Open |
| Web/WhatsApp conversations and research event records | Tenant-scoped; research collection consent-gated; no general deletion window yet | **Decision required before unrestricted traffic** | Open |
| Local backups | Format-3 checksummed backup with configured local retention | Operator-configured | Enforced locally; off-host policy open |
| Off-host backups | No provider or credential configured | **Decision and restore proof required** | Open |

Until the two durable product-data windows are approved and implemented, the
service remains a controlled pilot. A reviewer may choose a lawful window and
request the matching deletion/export implementation; approval must not merely
delete the words “decision required.”

## Data-subject and tenant-owner operations

Before unrestricted use, the operating process must name an owner and response
deadline for access, correction, deletion, export, consent withdrawal and legal
hold requests. A request must be authenticated, tenant-scoped, recorded without
putting raw identity data in tickets, and verified across the primary database,
generated artifacts and backups according to the approved backup policy.

Deletion must be suspended only by a documented legal or security hold. A hold
must identify its owner, scope, start time and review date; it must not silently
disable global cleanup.

## Incident and disclosure boundary

Operational evidence uses correlation IDs and tenant/user pseudonyms. Raw phone
numbers, customer addresses, message bodies, credentials and provider payloads may
not be pasted into issue trackers, chat, CI artifacts or release evidence. Any
suspected cross-tenant access, credential disclosure, unverified webhook processing
or retention failure is a security incident and follows `docs/runbook.md`.

## Approval record

Approval is valid only when every row below is completed in a reviewed repository
change and the unresolved retention decisions above have matching code and tests.

| Role | Name | Decision | UTC date | Reviewed revision | Notes |
| --- | --- | --- | --- | --- | --- |
| Privacy reviewer | _unassigned_ | pending | — | — | Lawful purpose, consent text, data-subject process and retention windows |
| Security/operations reviewer | _unassigned_ | pending | — | — | Access controls, deletion jobs, backups, recovery and incident boundary |

Changing an approver, retention window, purpose or data class creates a new policy
version. The old approval record remains immutable in repository history.
