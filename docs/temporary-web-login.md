# Temporary web-only login

## Evidence state

This document describes the temporary web-login release candidate. The
implementation and automated contracts are **implemented-tested** in the working
release boundary; production promotion, schema `0013_web_pin_challenges`, public
browser canaries and a deployed-SHA evidence record are **production pending**.
The existing production evidence in `docs/release-evidence-b35762a.md` predates
this feature and must not be used as evidence that temporary web login is live.

This is a containment mode while WhatsApp verification remains parked. It is not
the long-term authentication design and it is not equivalent to per-user identity
proof.

## Authentication modes

`AUTH_LOGIN_MODE` is an explicit kill switch and selector:

| Value                  | Behaviour                                                                                                                        |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `disabled`             | Both login request and verification fail closed. Use this for immediate containment.                                             |
| `whatsapp_otp`         | The existing short-lived, single-use WhatsApp OTP flow. It requires an activated WhatsApp backend.                               |
| `temporary_static_pin` | A shared six-digit pilot PIN verifies only a short-lived challenge for a currently mapped identity. No provider message is sent. |

Production temporary-PIN mode is valid only when all of the following are true:

- `WHATSAPP_BACKEND=disabled`;
- `META_TEST_SENDER_VERIFICATION_MODE=disabled`;
- proactive, daily and weekly WhatsApp insights are disabled;
- `TEMPORARY_WEB_PIN_EXPIRES_AT` is a future timezone-aware timestamp; and
- the verifier is supplied through the scoped Compose secret, never an inline
  environment variable.

Meta credentials remain in their existing scoped host secret boundary for a later
reviewed activation. Parking the provider does not authorize deleting, copying or
exposing those credentials. The disabled selector prevents the application from
using Meta for login, the test-sender lane or proactive delivery.

## Mapped-only invariant

Temporary login is available only when the submitted primary phone belongs to an
active user and has an approved, non-opted-out phone identity joined to an active
membership in an active tenant. A platform role alone is not enough. Requesting a
challenge returns the same accepted response for mapped and unmapped phones, and
verification returns a generic denial, so the endpoint is not a mapping oracle.

The request creates or retains a provider-free challenge with the normal ten-minute
TTL. Verification is single-use, attempt-bounded, Redis rate-limited by HMAC-derived
phone and trusted client-IP keys, and audit logged with a one-way phone reference.
Changing a phone, membership, tenant, opt-out or user to an ineligible state also
invalidates an existing session on its next authenticated use.

The shared PIN is only one factor shared by a small pilot group. Anyone who learns
both an approved phone and the PIN can act as that mapped user until the credential
is rotated or expires. Therefore it must not be reused elsewhere, sent in product
UI or logs, committed, placed in shell history, or treated as proof of possession
of the phone. Keep the allowlist small, use a short operational expiry, monitor
denied/success audit events and rotate immediately after suspected disclosure.

## Verifier boundary and lifecycle

`scripts/set_temporary_login_pin.sh` prompts on the controlling terminal without
echo, reads the existing production `OTP_SECRET`, and writes only:

```text
HMAC-SHA256(OTP_SECRET, "web-login-pin:" + PIN)
```

The raw PIN is never written by the script. The host verifier is a root-owned
`0600` regular file in its own root-owned `0700` directory, separate from the
deploy-user-owned Meta and Hermes `SECRETS_DIR`. Before deployment, the non-root
coordinator asks Docker to validate the verifier through the already-pulled exact
API image with no network, a read-only root filesystem, no capabilities and
`no-new-privileges`; the validation command never emits the verifier. A separate
networkless one-shot initializer copies it into a dedicated runtime volume as an
API-readable `0400` file. Web, worker, scheduler, Hermes and browser processes do
not receive that verifier. The API recomputes the candidate HMAC and compares it
in constant time.

The `bumpabestie` deployment account is deliberately trusted and root-equivalent
because it can control Docker. Root host ownership protects the verifier from the
application services, ordinary host processes and accidental direct reads; it is
not a security boundary against a malicious deployment operator. That operator
also controls the release and reads `OTP_SECRET`, so production access to the
account and its SSH key must be treated as privileged root access.

From the checked-out production repository, provision or rotate the verifier as
root. The setter reads the dedicated absolute
`TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST` from the environment file:

```bash
sudo ./scripts/set_temporary_login_pin.sh
sudo ./scripts/set_temporary_login_pin.sh /opt/bumpabestie/.env.production
```

The operator supplies the PIN only at the hidden prompt. Do not pass it as an
argument, environment variable or stdin captured by automation. Set a new future
`TEMPORARY_WEB_PIN_EXPIRES_AT`, validate the environment, and apply the change
through the guarded promotion procedure in `docs/deployment.md` so the initializer
and API are recreated together. A verifier rotation does not revoke already issued
session cookies; contain suspected compromise by setting `AUTH_LOGIN_MODE=disabled`,
using the normal session-revocation controls, and promoting the containment change.

The first rollout is intentionally two-phase. First set `AUTH_LOGIN_MODE=disabled`,
leave every `TEMPORARY_WEB_PIN_*` field blank, park WhatsApp delivery and promote
the compatible release. Verify that release and schema while login remains closed.
Only then set the fixed runtime path
`TEMPORARY_WEB_PIN_VERIFIER_FILE=/run/auth-secret/temporary_web_pin_verifier`, the
dedicated host path and future expiry, run the setter, and promote the same
immutable revision and six image digests again. This keeps the previous release a
viable pre-boundary rollback and prevents half-activation of the temporary mode.

Rollback is fail-closed: set `AUTH_LOGIN_MODE=disabled`, blank all four temporary
PIN fields and use the guarded promotion path. The root-owned dormant verifier may
remain on disk, but production Compose mounts `/dev/null` and the initializer
removes the API runtime copy outside temporary mode. Expiry independently disables
request and verification even if an operator forgets the kill switch. Do not
switch to `whatsapp_otp` until the Meta activation gates are complete. To restore a
prior temporary PIN during a controlled rollback, rerun the setter at its hidden
prompt and repeat the initializer/API promotion; never keep a raw rollback copy on
the host.

## Roles, hosts and cookies

Authentication and authorization remain separate:

- a mapped user receives only their active tenant membership access;
- `operator` grants the admin surface;
- `researcher` grants the consent-bounded research surface; and
- `superadmin` remains protected and is not created, granted or revoked by the
  ordinary platform-access lifecycle.

Only a superadmin may grant or revoke the independent `operator` and `researcher`
roles for an active mapped collaborator, and every mutation is audited. Temporary
login never elevates a user because they entered through the admin or research
hostname. While temporary login is active, both platform-access grant endpoints
require the target's primary phone to have an approved, non-opted-out mapping with
an active membership and tenant. The UI grants access only from that mapped
directory; map the collaborator in Tenant operations first.
Suspension blocks new grants but never blocks deprivileging an existing role holder;
both current and legacy revoke routes remain available for incident response.

Production cookies remain `Secure`, `HttpOnly`, `SameSite=Lax` and host-only because
`SESSION_COOKIE_DOMAIN` stays blank. The login destination is host-aware: an admin
host stays on the admin path, a research host stays on the research path, and the
public host chooses an authorized tenant or platform destination. A user must have
the matching role and may need to authenticate separately on another branded host;
the cookie is intentionally not widened across subdomains.

## Cloudflare client-IP boundary

Caddy is the only public listener. It accepts `CF-Connecting-IP` as the visitor IP
only when the socket peer belongs to the pinned official Cloudflare proxy ranges
and strict trusted-proxy parsing succeeds; otherwise it uses the direct socket
peer. Caddy overwrites the private single-IP `X-Bumpa-Client-IP` header and strips
the Cloudflare header before proxying. The Next.js backend-for-frontend rejects
lists and malformed values, forwards at most that validated address as
`X-Forwarded-For`, and never forwards a browser-supplied forwarding chain. FastAPI
uses the resulting address only for privacy-preserving rate-limit keys. Keep the
pinned Cloudflare ranges synchronized with the primary references before relying
on this control.

## Migration and verification

Migration `0013_web_pin_challenges` adds `temporary_web_pin` to the constrained OTP
session purpose set and a partial unique index that permits at most one unconsumed
temporary challenge per phone. It stores no raw PIN and introduces no new tenant
table. Its downgrade drops the index and deletes temporary challenges before
restoring the prior constraint; use the forward-only production rollback policy
rather than running a destructive downgrade during an incident.

Run the focused local contracts before the full release gate:

```bash
uv run --project apps/api pytest \
  apps/api/tests/test_temporary_web_pin_auth.py \
  apps/api/tests/test_platform_access.py \
  apps/api/tests/test_onboarding_migration.py
npm --prefix apps/web test -- \
  tests/login.test.tsx tests/phone.test.ts tests/backend-route.test.ts
./scripts/test_production_contract.sh
./scripts/validate_env.sh .env.example local
TEMPORARY_AUTH_E2E_PIN=<six-digit-disposable-test-pin> make temporary-auth-e2e
make quality
make compose-smoke
make e2e-linux
```

The exact command syntax is subordinate to the repository's pinned tool versions;
CI is authoritative. Production acceptance additionally requires exact-SHA CI and
image publication, migration-head proof, five redacted mapped-login successes,
unmapped and wrong-PIN negative canaries, role/host denial canaries, confirmation
that no WhatsApp send/outbox count changed, public desktop/mobile browser checks,
service stability and a new redacted release-evidence artifact.
