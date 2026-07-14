# Production release evidence — 0ec2c58

This is the redacted operator record for the production web release
`0ec2c58f8b0a26734ca08788787640dca1409821`, promoted on 2026-07-14. It
contains no credentials, authentication values, raw phone mappings, tenant
identifiers, origin addresses, administrator network ranges, secret-file paths,
environment values or message bodies.

The release activates a contained, provider-free web sign-in path for the five
already mapped collaborators. WhatsApp authentication and delivery remain
explicitly parked; this record is not evidence of Meta activation.

## Release chain

- Temporary web-login [PR 45](https://github.com/makriman/bumpa-rct-app/pull/45)
  merged as `0ec2c58f8b0a26734ca08788787640dca1409821`.
- Exact-revision [main CI 29333098858](https://github.com/makriman/bumpa-rct-app/actions/runs/29333098858)
  passed 13/13 jobs.
- Exact-revision [publication 29333505495](https://github.com/makriman/bumpa-rct-app/actions/runs/29333505495)
  passed 7/7 jobs. Publication-gate checks and scans passed for all six images,
  and every published image carries the exact successor revision label.

| Service              | Deployed OCI index reference                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------- |
| API/worker/scheduler | `ghcr.io/makriman/bumpabestie-api@sha256:5510ce54bcb5b537b429a34c7bb59a02916805b903bbf664bed0985e992ccb8b`      |
| Web                  | `ghcr.io/makriman/bumpabestie-web@sha256:28da9b1dcdc8bdb444d700980b3df00c853c6438afd21dae9f6cb6b5c4db6cd3`      |
| Caddy                | `ghcr.io/makriman/bumpabestie-caddy@sha256:690f3b0ead0d903c2e78fd50a12813c7f798d0a43f44e3be9f0123929cac3d33`    |
| PostgreSQL           | `ghcr.io/makriman/bumpabestie-postgres@sha256:b345ff05ebbd21ab28c25ab823beeb1fd7884d7848a9350ad03598d43dbae5b4` |
| Backup               | `ghcr.io/makriman/bumpabestie-backup@sha256:c20599f39fc4da1f71194ee6e0c53b79ae64a27d634c5c5c18163da0dee6327a`   |
| Hermes               | `ghcr.io/makriman/bumpabestie-hermes@sha256:3397b243ce95e7875b2382269ca574e59376da5c1b6e809772cc94baf4e6f449`   |

The deployed release record, successor-image container references, revision labels
and published indexes matched this exact successor and these exact digests. Redis
matched its separately pinned upstream digest.

## Guarded promotion and secret boundary

- The production prerequisites were installed from the immutable successor and
  passed byte-identity, syntax and narrow-privilege validation.
- Phase one promoted the exact successor with web login disabled and all
  temporary-authentication fields blank. Independent acceptance confirmed the
  release record, exact images, runtime health and schema before activation.
- Phase two staged the verifier through a hidden terminal prompt, validated its
  root-owned versioned source, and promoted the same revision and digests with the
  temporary web-login boundary active.
- Only the API receives the read-only runtime verifier. Web, worker, scheduler and
  Hermes do not receive it. The verifier value, source and runtime paths were not
  captured in this evidence.
- WhatsApp, Meta test-sender verification and proactive, daily and weekly outbound
  delivery remained disabled in both phases.
- Both promotions completed without a coordinator journal, maintenance interlock
  or promotion-state artifact left active.

## Runtime and data boundary

- The eight long-running services used their intended immutable references: seven
  from the promoted application indexes above and Redis from its separately pinned
  upstream digest. The backup index was exercised by the successful one-shot
  wrapper. Every long-running service reported zero restarts, zero OOM kills and no
  unhealthy state; readiness reported database, Redis, worker and scheduler healthy.
- The transactional migration gate completed at schema
  `0013_web_pin_challenges`.
- The eligible set remained exactly five pre-existing mapped collaborators. The
  role aggregates were four operators, four researchers and one superadmin;
  sign-in did not grant or modify any role.
- The active-authentication baseline was zero before acceptance. After the
  acceptance run and cleanup checks, active sessions and temporary challenges
  were again zero.
- The release record, auth boundary, image/health state, firewall persistence and
  backup-timer state passed the independent final invariant check.

## Web authentication acceptance

The acceptance matrix covered every eligible collaborator on each of the three
host-scoped web surfaces: public chat, platform administration and research. It
completed all 15 user/surface combinations, with five sign-ins exercised through
real browsers and ten through the production API/BFF contract.

- Only an existing eligible mapping received a persisted challenge and could
  complete sign-in. Unmapped requests and wrong-code verification returned the same
  public response/error shapes as their mapped counterparts, without
  identity-specific fields.
- The country selector supported the required international dialing prefixes and
  normalized input before the mapped-user check.
- In every authorized matrix case, a correct sign-in reached the host's protected
  destination. Administration and research access remained role-gated;
  authentication alone did not confer either role.
- The session cookie remained host-only and did not authenticate a sibling host.
  Logout revoked the session and returned the user to that host's sign-in page.
- Public chat, administration and research were inspected at desktop and mobile
  widths. The authenticated destinations rendered without horizontal overflow;
  the mobile administration navigation exposed a working logout action.

No authentication value, cookie or mapped identity was retained in this record.

## Cloudflare and origin boundary

- The apex, API, administration and research HTTPS hosts routed correctly through
  Cloudflare; `www` redirected canonically to the apex while preserving path and
  query.
- Edge responses carried HSTS, content-type sniffing protection, referrer policy,
  permissions policy and request-specific document CSP nonces.
- TLS 1.0 and 1.1 were rejected; TLS 1.2 and 1.3 were accepted.
- Direct-origin HTTP and HTTPS probes were blocked. The persistent Cloudflare-only
  host and Docker firewall layers, including their pre-gate, passed independent
  verification.

## WhatsApp containment

- WhatsApp authentication and delivery were not used for this release.
- The operational WhatsApp/outbox fingerprint was identical before and after the
  web-authentication acceptance matrix.
- No Meta test-sender verification, OTP send, proactive insight or scheduled
  outbound delivery was enabled.

WhatsApp verification and activation are intentionally deferred to a later,
separately evidenced release.

## Post-release backup

- The guarded systemd backup wrapper completed successfully after promotion,
  quiesced and resumed every data-writing service under the maintenance lock, and
  left all eight services running.
- The new recovery point contains exactly the expected PostgreSQL dump, exports,
  Hermes runtime, Hermes staging and manifest artifacts. All five recorded SHA-256
  values had valid shape and replayed successfully.
- Its format-3 manifest records PostgreSQL 16.14 and binds revision
  `0ec2c58f8b0a26734ca08788787640dca1409821`, schema
  `0013_web_pin_challenges` and the exact backup-image digest above. Its creation
  timestamp falls within the successful guarded service run.
- The backup timer remains active.

This closes the exact-successor **local** recovery-point gate only. Encrypted
off-host copy and isolated remote restore evidence remain open.

## Stability observation

- At the closing gate, every long-running container had at least 20 minutes of
  continuous uptime. Five additional live samples retained the same eight container
  identities, their exact expected images, seven healthy configured healthchecks,
  zero restarts and zero OOM kills.
- Schema remained `0013_web_pin_challenges`; database, Redis, worker and scheduler
  readiness stayed healthy; the full public host smoke passed with WhatsApp still
  reported disabled.
- The bounded 20-minute log review found zero severe matches and zero exit-signal,
  out-of-memory or crash signatures.
- The persistent Cloudflare-only firewall and origin pre-gate passed, backup and
  disk timers remained active/enabled, and the maintenance lock was available with
  no coordinator journal or promotion interlock.

## External launch gates

This record proves the contained web release, not unrestricted provider-backed
activation. The remaining gates are explicit:

- complete Meta Business/sender verification and separately prove WhatsApp
  authentication and delivery before switching away from the parked boundary;
- restore and evidence complete Bumpa dataset coverage;
- configure and exercise encrypted off-host backup and an isolated restore;
- configure and verify a real external alert destination and signed receipt; and
- obtain the remaining privacy/security/retention approval before unrestricted
  launch.
