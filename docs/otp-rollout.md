# WhatsApp OTP release gate

The OTP workflow already exists in the production application: authentication
routes and identity policy in `app/routes/auth.py` and `app/core/security.py`,
Meta authentication-template delivery in `app/providers/meta.py`, and the three
web sign-in surfaces. The original provider integration was delivered in PR17;
PR43 introduced the temporary mapped-user PIN while Meta authentication remained
unavailable. This release strengthens that workflow rather than introducing a
replacement provider or public registration.

## Code and identity guarantees

- Production OTP requests and session use retain the current pilot boundary:
  active users, approved non-opted-out phone identities, active tenant membership
  and active tenant. A platform role alone does not add a production login.
- Codes use cryptographically random six-digit values in production and are
  persisted only as OTP-secret-peppered hashes. Local fixed codes remain limited
  to local/test mode. Authentication secrets never enter browser bundles.
- Issuance and verification serialize using per-phone transaction advisory locks,
  without adding UPDATE privileges on user records. Only the newest
  login-purpose challenge can be verified; resending consumes prior login codes.
  Other challenge purposes cannot affect login cooldown or code verification.
- Expiry is exclusive, consumption is single-use, and failed attempts serialize
  under the same database lock. Existing phone/IP Redis rate limits and
  fail-closed Redis outage handling remain active. A cooldown duplicate does not
  send another message or reveal approved-phone eligibility.
- The API returns the resend cooldown. All three sign-in surfaces offer bounded
  resend; the consumer clears an old entered code after resend and preserves a
  successfully created session when workspace lookup needs retrying.
- Cookie flags, host boundaries, tenant selection, role authorization and
  research consent are unchanged. OTP activation does not enroll new merchants.

## Provider prerequisite

Successful readiness only proves dependencies and configured adapters. Before
OTP activation, enumerate the configured WABA's complete template inventory,
including pagination, and require an **APPROVED AUTHENTICATION** template whose
name and language match the application. A utility/marketing template is not an
OTP substitute. Prefer an existing matching approved authentication template;
if none exists, submit the smallest copy-code authentication template only after
the owner approves its exact account/name/language/expiry and purpose.

[Meta authentication templates](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/authentication-templates/authentication-templates)
and [copy-code templates](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/authentication-templates/copy-code-button-authentication-templates)
describe the supported path. The code in the body and button must match. A
copy-code button suits the browser-based app without adding an Android package
binding. Keep the template's expiry notice aligned with `OTP_TTL_MINUTES`.

Inspect sender connection and verification fields separately.
`code_verification_status=EXPIRED` by itself does not establish that a currently
`CONNECTED` Cloud API sender cannot deliver. Do not re-register it, reset its
two-step security PIN or rotate credentials from that field alone. Use read-only
account/sender inspection and an authorized recipient canary to distinguish
template, delivery, verification and registration failures. No code request,
registration, template submission or credential change is part of a read-only
preflight.

## Exact proposed cutover

After provider approval, exact-SHA CI/publication, verified rollback and the
explicit action-time auth confirmation:

1. Select `AUTH_LOGIN_MODE=whatsapp_otp` and retain the existing Meta sender,
   mapped identities, roles and disabled proactive/daily/weekly delivery flags.
2. Blank `TEMPORARY_WEB_PIN_VERIFIER`, `TEMPORARY_WEB_PIN_VERIFIER_FILE`,
   `TEMPORARY_WEB_PIN_VERIFIER_FILE_HOST` and `TEMPORARY_WEB_PIN_EXPIRES_AT` in
   the staged environment. Preserve root-owned prior verifier files for recovery.
   Keep `META_OTP_TEMPLATE_NAME` and `META_TEMPLATE_LANGUAGE_CODE` matched to the
   approved authentication template. Do not reset or extend the shared web PIN.
3. Activate through the supported promotion coordinator. Its auth initializer
   removes the PIN verifier from the API runtime volume for OTP mode.
4. With a specifically authorized synthetic mapped owner/recipient, verify +234
   delivery and provider IDs/status, expiry, resend, single-use verification,
   host-only cookies, workspace roles, logout, opt-out/revocation and tenant
   isolation. Preserve no raw phone/code/provider-token values in evidence.

The affected group is the currently mapped pilot on all three web surfaces. They
will need possession of their mapped WhatsApp account instead of the shared PIN.
Existing cookies are not automatically revoked by changing the login method;
any proposed revocation needs explicit scope. Delivery outage can prevent new
logins; no automatic PIN fallback is introduced. The current prior auth expiry
and recorded/running image boundary must be reconciled before promising rollback.

## Separation and evidence

Merchant business setup is independent from login-factor selection and remains
available to authorized WhatsApp identities. Code/images can be prepared while
OTP is pending, but that is not an end-to-end production rollout. Do not bypass
the coordinator, remove working pilot access or silently restore an expired PIN
to force an onboarding-only release. A different containment/rollback auth
boundary needs explicit approval and verification.

Local coverage includes provider-failure sanitization, cooldown privacy, resend
supersession, purpose isolation, expiry, mapping and replay. The protected CI
backend additionally runs `verify_release_auth_onboarding.py` against real
PostgreSQL using a generated non-bypass role, including concurrent OTP issuance,
single consumption, bounded attempts, HTTP onboarding and tenant/member denial.
These checks use only synthetic fixtures and mock providers; they do not prove
Meta delivery or actual Hermes response quality.
