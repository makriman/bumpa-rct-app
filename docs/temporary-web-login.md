# Temporary web-only login

## Evidence state

This document describes the temporary web-login boundary carried by application
release `c0c15443352ab84fde1d2edfde1ed0692ed842f6`. Exact-revision
[merged-main CI 29412671738](https://github.com/makriman/bumpa-rct-app/actions/runs/29412671738)
passed 13/13 jobs,
[image publication 29413085773](https://github.com/makriman/bumpa-rct-app/actions/runs/29413085773)
passed 7/7 jobs, all six immutable indexes were promoted through the guarded
coordinator, and schema `0015_bumpa_store_context` is live. Older evidence may
describe the initial feature rollout but predates this application boundary and
current reverification.

This is a containment mode while WhatsApp verification remains parked. It is not
the long-term authentication design and it is not equivalent to per-user identity
proof.

## Production acceptance

The live allowlist contains exactly five approved mapped collaborators. Redacted
acceptance canaries proved mapped login and generic wrong/unmapped denial without
recording any identity or credential. The current in-app-browser audit exercised
the public homepage/login, searchable flag/calling-code selector and national-number
field at desktop and mobile widths without horizontal overflow. The separate
production BFF matrix completed all 15 collaborator/surface sign-ins across apex,
admin and research and reached each protected destination; it is HTTP/DOM contract
evidence, not a retained authenticated screenshot set.

Authentication remained separate from authorization: the apex accepted an active
mapped membership, while admin and research access still required their independent
platform roles. Cookies remained host-only, did not cross subdomains and were
revoked by logout. After canary cleanup, the database contained zero active
temporary challenges and zero active acceptance sessions.

WhatsApp verification, test-sender verification and proactive/daily/weekly
WhatsApp delivery remained disabled throughout. This evidence does not imply a
Meta send, OTP or delivery receipt.

## Authentication modes

`AUTH_LOGIN_MODE` is an explicit kill switch and selector:

| Value                  | Behaviour                                                                                                                        |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `disabled`             | Both login request and verification fail closed. Use this for immediate containment.                                             |
| `whatsapp_otp`         | The existing short-lived, single-use WhatsApp OTP flow. It requires an activated WhatsApp backend.                               |
| `temporary_static_pin` | A shared six-digit pilot PIN verifies only a short-lived challenge for a currently mapped identity. No provider message is sent. |

Production temporary-PIN mode is valid only when all of the following are true:

- WhatsApp is either fully parked (`WHATSAPP_BACKEND=disabled` and
  `META_TEST_SENDER_VERIFICATION_MODE=disabled`) or limited to the signed,
  `inbound_replies_only` Meta test-sender lane with
  `META_PRIMARY_SENDER_ENABLED=false`;
- proactive, daily and weekly WhatsApp insights are disabled;
- `TEMPORARY_WEB_PIN_EXPIRES_AT` is a future timezone-aware timestamp; and
- the verifier is supplied through the scoped Compose secret, never an inline
  environment variable.

Meta credentials remain in their scoped host secret boundary. Parking the provider
does not authorize deleting, copying or exposing those credentials. When the
reply-only test lane is enabled alongside the temporary PIN, its exact test WABA
and phone-number pair is the sole accepted WhatsApp sender: it cannot send OTPs,
initiate proactive messages or activate the configured production sender.

## Mapped-only invariant

Temporary login is available only when the submitted primary phone belongs to an
active user and has an approved, non-opted-out phone identity joined to an active
membership in an active tenant. A platform role alone is not enough. Requesting a
challenge returns the same accepted response for mapped and unmapped phones, and
verification returns a generic denial without identity-specific fields. This is a
public-shape guarantee, not a claim about network-level timing resistance.

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

The raw PIN is never written by the script. Each rotation is a new root-owned
`0600` regular file under
`/var/lib/bumpabestie-auth-secret/temporary-web-pin-verifiers/`; both parent
directories are root-owned `0700`. The random 32-hex filename is a non-secret
version identifier, never a verifier, hash or PIN. The files are separate from
the deploy-user-owned Meta and Hermes `SECRETS_DIR`. Before deployment, the non-root
coordinator invokes the fixed root-owned
`/usr/local/sbin/bumpabestie-validate-temporary-auth-secret` helper through one
non-interactive sudoers command. The helper, never the mutable checkout copy,
validates canonical path components, root ownership, private modes, single-link
regular-file shape and exact content. It then asks Docker to mount only that exact
file into the already-pulled API image with no network, a read-only root filesystem,
no capabilities and `no-new-privileges`; the validation command never emits the
verifier. A separate
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

The sudoers rule authorizes only the fixed installed helper. The helper requires
exactly three non-secret arguments, accepts only explicit login modes, one fixed
legacy path or the non-overwriting versioned path, and an exact image digest. It never
sources `.env.production`. Bootstrap validates the rule with `visudo`, installs it
root-owned at mode `0440`, and installs the helper root-owned at mode `0755`.

From the checked-out production repository, provision or rotate the verifier as
root:

```bash
sudo ./scripts/set_temporary_login_pin.sh
sudo ./scripts/set_temporary_login_pin.sh /opt/bumpabestie/.env.production
```

The setter takes the maintenance lock and requires a private valid deployed-release
record. For an active temporary boundary, the environment must still select the
exact recorded host path; a different path means a rotation is already staged and
is rejected before the prompt. The setter silently validates the retained deployed
file's exact shape, owner and mode, prompts without echo, creates a distinct file
with exclusive-create semantics, fsyncs the file and directories, then atomically
rewrites only `TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST` in `.env.production`. It never
overwrites or removes an older verifier.

The operator supplies the PIN only at the hidden prompt. Do not pass it as an
argument, environment variable or stdin captured by automation. For rotation,
leave the current host path unchanged, set a new future
`TEMPORARY_WEB_PIN_EXPIRES_AT`, run the setter, validate the environment, and apply
the change through the guarded promotion procedure in `docs/deployment.md` so the
initializer and API are recreated together. A verifier rotation does not revoke
already issued session cookies; contain suspected compromise by setting
`AUTH_LOGIN_MODE=disabled`, using the normal session-revocation controls, and
promoting the containment change.

The first rollout is intentionally two-phase. First set `AUTH_LOGIN_MODE=disabled`,
leave every `TEMPORARY_WEB_PIN_*` field blank, park WhatsApp delivery and promote
the compatible release. Verify that release and schema while login remains closed.
Only then set the fixed runtime path
`TEMPORARY_WEB_PIN_VERIFIER_FILE=/run/auth-secret/temporary_web_pin_verifier`, the
temporary mode, a future expiry, and a blank
`TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST`; run the setter so it creates and selects
the first version, then promote the same
immutable revision and six image digests again. This keeps the previous release a
viable pre-boundary rollback and prevents half-activation of the temporary mode.
The phase-one release record stores the disabled non-secret auth boundary. A failed
phase-two promotion atomically restores that boundary even though its revision and
image digests are unchanged, reruns `auth-secret-init` to remove the runtime verifier,
and recreates the API only after the initializer succeeds. Legacy release records
without an auth object default to this disabled boundary.
The rollback path removes public Caddy ingress and the failed target API before it
pulls or initializes anything. Any failed pull, initializer, recreation, or smoke
attempt removes them again and sets the maintenance interlock, so a failed target
login mode is never kept live merely to preserve availability.

Manual containment is fail-closed: set `AUTH_LOGIN_MODE=disabled`, blank all four temporary
PIN fields and use the guarded promotion path. The root-owned dormant verifier may
remain on disk, but production Compose mounts `/dev/null` and the initializer
removes the API runtime copy outside temporary mode. Expiry independently disables
request and verification even if an operator forgets the kill switch. Do not
switch to `whatsapp_otp` until the Meta activation gates are complete. A failed
rotation restores the recorded prior host path, runs `auth-secret-init` against
that retained versioned file, and recreates the prior API only afterward; it never
asks an operator to reconstruct the old PIN. Legacy fixed-path records remain
accepted solely for rollout compatibility, while every new setter run selects a
versioned file.

If the selected host verifier is missing or suspected compromised, do not claim
that a promotion preflight provides immediate containment. Follow the root-only
emergency procedure in the runbook to remove Caddy and API, prove both are absent
through Docker labels and leave the maintenance interlock active. That deliberate
outage remains in place until a reviewed recovery reconciles the recorded auth
boundary; the ordinary disabled-mode promotion is for a healthy pre-boundary site.

Retain older verifier files conservatively. They are tiny, private recovery
material referenced by release history and rollback evidence. This release has no
automated deletion command; do not remove one merely because a newer version is
live. Retirement requires a later separately reviewed policy that proves the path
is absent from the active environment, deployed release, in-flight journals,
retained rollback window and protected recovery evidence.

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
CI is authoritative. For application release
`c0c15443352ab84fde1d2edfde1ed0692ed842f6`,
exact-SHA CI/image publication, migration-head proof, the five-mapping acceptance
set, generic unmapped and wrong-PIN denials, role/host boundaries, disabled
WhatsApp state and public desktop/mobile browser checks are production evidence for
this bounded web-login mode only.

Still unproven and outside this release decision are WhatsApp sender/template and
delivery activation, per-user proof of phone possession, complete provider-backed
business journeys, an encrypted off-host restore, external alert delivery and
formal privacy/security/retention approval. Do not describe this temporary bridge
as completion of the original build plan or as authorization for unrestricted
provider-backed traffic.
