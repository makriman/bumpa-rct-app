# Production release evidence — b35762a

This is the redacted operator record for production release
`b35762ab2a9d5c1a4956530cae63040354805510`, promoted on 2026-07-13 UTC. It
contains no credentials, bearer tokens, raw tenant identifiers, phone mappings or
message bodies. GitHub links provide the durable build evidence; the remaining
entries record read-only or guarded checks run against the production Droplet and
Cloudflare edge.

## Release chain

- Delivery-hardening [PR 41](https://github.com/makriman/bumpa-rct-app/pull/41)
  merged as `b35762ab2a9d5c1a4956530cae63040354805510`.
- Pre-merge [PR CI 29290441375](https://github.com/makriman/bumpa-rct-app/actions/runs/29290441375)
  passed 13/13 jobs on `5644fd596c1292e3f8c0505fbb80109c4f556bae`.
  The PR-head and merge-commit trees are identical.
- Exact-revision [main CI 29290795169](https://github.com/makriman/bumpa-rct-app/actions/runs/29290795169)
  passed 13/13 jobs.
- Exact-revision [publication 29291129708](https://github.com/makriman/bumpa-rct-app/actions/runs/29291129708)
  passed 7/7 jobs. Each published OCI index has exactly one Linux/amd64 runtime
  manifest, an attestation manifest and the exact revision label.

| Service              | Deployed OCI index reference                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------- |
| API/worker/scheduler | `ghcr.io/makriman/bumpabestie-api@sha256:b34035d69563609ceb6cb28ec19364c3d145146e6ce48b88190fecea84953782`      |
| Web                  | `ghcr.io/makriman/bumpabestie-web@sha256:54df7e9217bbbc8f280de1220cd723fa9c0003205c3ff80e435a38a0f3c84e01`      |
| Caddy                | `ghcr.io/makriman/bumpabestie-caddy@sha256:97002da6d6f72af826fdc9c5d5bc7d28400dbce01bbe442ea6000b159647816f`    |
| PostgreSQL           | `ghcr.io/makriman/bumpabestie-postgres@sha256:356ea8249b6d7a9ac73567543b19937d8702f8a12032794a643e59bce64ecc56` |
| Backup               | `ghcr.io/makriman/bumpabestie-backup@sha256:5333c066e1680e8db6a0748ec6e6bc9bb9cb16d907c1540bdaf48dbcd3cf0158`   |
| Hermes               | `ghcr.io/makriman/bumpabestie-hermes@sha256:2c0b4f1760a209e3d3c8b95afa158f73f3ed4014594a39efcac3ae5f0f04dcfc`   |

The deployed release record, running container image references and published
indexes matched exactly.

## Promotion and recovery points

- The root-owned guarded coordinator fetched exact `origin/main`, validated the
  production environment and image labels, and created pre-promotion backup
  `20260713T225544Z`.
- The pre-promotion backup passed all five SHA-256 entries before any image was
  rotated.
- PostgreSQL 16.14 restarted on the new digest; schema
  `0012_operational_retention` was already current and the transactional migration
  gate passed.
- Both direct-origin and Cloudflare-edge smoke passed during promotion: health,
  readiness, apex 200, `www` 308, admin/research 307 and two distinct document CSP
  nonces.
- The coordinator committed the release at 22:58:12Z with no journal,
  maintenance interlock, promotion-state file or lock holder left behind.
- Guarded post-release backup `20260713T230602Z` passed its format-3 manifest and
  all five SHA-256 entries. Its manifest records the exact application revision,
  schema, PostgreSQL 16.14 and backup digest above. Backup and disk-usage timers
  remained enabled and active.

## Cloudflare and public surface

- The apex, `www`, API, admin and research A records are proxied through
  Cloudflare to the production origin.
- Edge-to-origin encryption is Full (strict); Always Use HTTPS is on; minimum TLS
  is 1.2; TLS 1.3 is on; 0-RTT and Rocket Loader are off.
- Every host rejected TLS 1.0/1.1 and accepted TLS 1.2/1.3. Every HTTP probe
  returned an edge 301 to the same HTTPS path/query.
- `www` returned a 308 to the apex while preserving path and query.
- All five hosts returned Cloudflare headers, one CSP header, HSTS,
  `X-Content-Type-Options`, `X-Frame-Options`, referrer policy, permissions policy
  and cross-origin opener policy.
- Dynamic web documents returned unique request nonces, matching nonce-bearing
  scripts, no script `unsafe-inline`/`unsafe-eval`, no leaked `X-Nonce`, and
  `private, no-store`.
- Canonical and Open Graph URLs, the SVG icon, `robots.txt` and the five-route
  sitemap matched the apex production origin. The public landing and sign-in pages
  were visually inspected without submitting an OTP request.

## Runtime, tenancy and mappings

- Eight services were running. API, web, worker, scheduler, Hermes, PostgreSQL and
  Redis were healthy; Caddy was running without a configured Docker healthcheck.
- Every service had zero restarts and `OOMKilled=false`; the three init containers
  had exit code 0.
- Readiness returned `ready`, database/Redis/worker/scheduler `ok`, and selectors
  `meta`, `bumpa`, `hermes`.
- The non-bypass `bumpabestie_app` role had neither superuser nor bypass-RLS
  privilege. All 23 tenant tables had ENABLE+FORCE RLS and exactly one policy.
- Five active tenants across 115 tenant/table contexts exposed 670 scoped rows and
  zero cross-tenant rows; a missing tenant context exposed zero rows.
- Each tenant had exactly one active owner, approved non-opted-out phone identity,
  active Bumpa connection and active Hermes profile. Exactly one approved platform
  operator also held an owner role, as designed.

## Provider canaries

- Five read-only Bumpa verification requests passed. The latest durable sync
  evidence retained orders availability with seven or eight of ten analytics
  datasets available; missing datasets remain unavailable rather than zero.
- All five Hermes profiles passed readiness and one synthetic live Claude response
  containing no tenant business data.
- GET-only Meta Graph checks matched the configured test sender to its WABA. The
  sender remains `PENDING`, reply-only and `supports_otp=false`, with five approved
  non-authentication templates and zero authentication templates.
- Proactive/daily/weekly outbound insights remained disabled. The outbound
  WhatsApp row fingerprint was identical before and after the canaries. No OTP or
  WhatsApp message was sent.

## Stability observation

The read-only observation ran from 23:08:07Z through 23:18:46Z (10m39s), after the
release and final backup. Intermediate samples at 23:09:37, 23:10:47, 23:11:52,
23:12:58, 23:14:03, 23:15:14, 23:16:26 and 23:17:32Z showed no deviation.

- Start and end: 8/8 running, 7/7 configured healthchecks healthy, zero restarts,
  zero OOM kills and zero recent failing health probes.
- Every sample: readiness `ready`; database, Redis, worker and scheduler `ok`;
  providers `meta`, `bumpa`, `hermes`.
- End: no coordinator journal, maintenance interlock, promotion state/process or
  maintenance-lock holder.
- Sanitized log scan since 22:58:12Z found zero severe-event matches and zero
  exit-signal matches across Caddy, API, web, worker, scheduler, Hermes, PostgreSQL
  and Redis.

## External launch gates

This release proves the application and production platform, not unrestricted
provider-backed user activation. The remaining gates are explicit:

- complete Meta Business/sender verification and obtain an approved authentication
  template before OTP or real user sign-in;
- restore complete Bumpa dataset coverage;
- configure and exercise encrypted off-host backup (`OFFSITE_BACKUP_SCRIPT` is
  unset);
- configure and verify a real external alert destination and signed receipt; and
- obtain the remaining privacy/retention approval before unrestricted launch.
