"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Brand } from "@/components/ui";
import {
  homeForRole,
  resolvePostLoginDestination,
  safeNextPath,
  type AccessSurface,
} from "@/lib/navigation";
import type { Role } from "@/lib/demo-data";
import { apiRequest, demoFallbackEnabled } from "@/lib/api";
import {
  assembleE164,
  countryByIso,
  maskedPhone,
  PHONE_COUNTRIES,
} from "@/lib/phone";

type LoginStep = "phone" | "pin" | "session" | "unauthorized";
type PendingAction = "request" | "verify" | "session" | null;
type LoginDelivery = "web_pin" | "whatsapp";

type AccessRequestResponse = {
  delivery: LoginDelivery;
  expires_in_seconds: number;
  dev_code?: string | null;
};

type SessionResponse = {
  platform_roles: string[];
  memberships: Array<{ role: string; status: string }>;
};

const ACCESS_DENIED_COPY: Record<
  AccessSurface,
  { heading: string; description: string; guidance: string }
> = {
  workspace: {
    heading: "You don’t have workspace access.",
    description:
      "Your sign-in succeeded, but this account does not have an active Bumpa Bestie workspace membership.",
    guidance:
      "Ask a workspace owner or administrator to activate your membership, then check access again.",
  },
  admin: {
    heading: "You don’t have admin access.",
    description:
      "Your sign-in succeeded, but this account is not assigned an administrator role.",
    guidance:
      "Ask a superadministrator to grant admin access, then check access again.",
  },
  research: {
    heading: "You don’t have research access.",
    description:
      "Your sign-in succeeded, but this account is not assigned a researcher role.",
    guidance:
      "Ask a superadministrator to grant research access, then check access again.",
  },
};

export default function LoginPage() {
  const [step, setStep] = useState<LoginStep>("phone");
  const [countryIso, setCountryIso] = useState(
    demoFallbackEnabled ? "NG" : "GB",
  );
  const [nationalNumber, setNationalNumber] = useState(
    demoFallbackEnabled ? "0801 234 5678" : "",
  );
  const [requestedPhone, setRequestedPhone] = useState("");
  const [delivery, setDelivery] = useState<LoginDelivery | null>(null);
  const [expiresInSeconds, setExpiresInSeconds] = useState(0);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<PendingAction>(null);
  const [deniedSurface, setDeniedSurface] =
    useState<AccessSurface>("workspace");
  const pinRef = useRef<HTMLInputElement | null>(null);
  const country = countryByIso(countryIso);

  useEffect(() => {
    if (step === "pin") pinRef.current?.focus();
  }, [step]);

  const requestAccess = async (event?: FormEvent) => {
    event?.preventDefault();
    const phone = assembleE164(country, nationalNumber);
    if (!phone.ok) {
      setError(phone.error);
      return;
    }

    setPending("request");
    setError("");
    try {
      const result = await apiRequest<AccessRequestResponse>(
        "/auth/request-otp",
        {
          method: "POST",
          body: JSON.stringify({ phone_e164: phone.e164 }),
        },
      );
      if (result.delivery !== "web_pin" && result.delivery !== "whatsapp") {
        throw new Error("The configured sign-in method is unavailable.");
      }
      setRequestedPhone(phone.e164);
      setDelivery(result.delivery);
      setExpiresInSeconds(result.expires_in_seconds);
      setPin("");
      setStep("pin");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "We could not check this number. Try again.",
      );
    } finally {
      setPending(null);
    }
  };

  const enter = (role: Role) => {
    document.cookie = "bb_session=demo; path=/; SameSite=Lax";
    document.cookie = `bb_demo_role=${role}; path=/; SameSite=Lax`;
    const params = new URLSearchParams(window.location.search);
    window.location.href =
      safeNextPath(params.get("next")) ?? homeForRole(role);
  };

  const resolveSession = async () => {
    const me = await apiRequest<SessionResponse>("/auth/me");
    const next = new URLSearchParams(window.location.search).get("next");
    const destination = resolvePostLoginDestination({
      hostname: window.location.hostname,
      next,
      platformRoles: me.platform_roles,
      memberships: me.memberships,
    });
    if (!destination.authorized) {
      setDeniedSurface(destination.surface);
      setStep("unauthorized");
      return;
    }
    window.location.href = destination.path;
  };

  const retrySession = async () => {
    setPending("session");
    setError("");
    try {
      await resolveSession();
    } catch {
      setError(
        "You are signed in, but we could not open your workspace. Try again.",
      );
    } finally {
      setPending(null);
    }
  };

  const verify = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!/^\d{6}$/.test(pin)) {
      setError("Enter the complete six-digit web PIN.");
      return;
    }

    setPending("verify");
    setError("");
    try {
      await apiRequest("/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone_e164: requestedPhone, code: pin }),
      });
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "That PIN could not be verified.",
      );
      setPending(null);
      return;
    }

    setStep("session");
    setPin("");
    setPending("session");
    try {
      await resolveSession();
    } catch {
      setError(
        "Your PIN was accepted, but we could not open your workspace. Try again without entering the PIN again.",
      );
    } finally {
      setPending(null);
    }
  };

  const changeNumber = () => {
    setStep("phone");
    setDelivery(null);
    setPin("");
    setError("");
  };

  return (
    <main className="auth-page" id="main-content">
      <section className="auth-art">
        <Brand />
        <blockquote className="auth-quote">
          “I stopped guessing which products needed my attention. Now I ask, and
          I know.”<small>— Early pilot SME owner, Lagos</small>
        </blockquote>
        <div className="auth-trust-line">
          Private workspace · Tenant-isolated data · Clear freshness
        </div>
      </section>
      <section className="auth-main">
        <div className="auth-card">
          <Link href="/" className="brand auth-brand">
            <span className="brand-mark">B</span>
            <span className="brand-word">Bumpa Bestie</span>
          </Link>

          {step === "phone" ? (
            <>
              <span className="auth-kicker">Temporary web access</span>
              <h1>Welcome back.</h1>
              <p>
                Sign in with the mobile number approved for your Bumpa Bestie
                workspace. Only mapped team members can continue.
              </p>
              <div className="auth-notice" role="note">
                <span aria-hidden="true">↗</span>
                <div>
                  <strong>Pilot access</strong>
                  <span>
                    If WhatsApp verification is paused for your workspace, you
                    will be prompted for the temporary six-digit web PIN.
                  </span>
                </div>
              </div>

              <form onSubmit={requestAccess} noValidate>
                <fieldset className="phone-fieldset">
                  <legend>Approved mobile number</legend>
                  <div className="phone-fields">
                    <div className="field phone-country-field">
                      <label htmlFor="country">Country or region</label>
                      <select
                        id="country"
                        className={`select ${error ? "input-error" : ""}`}
                        value={countryIso}
                        onChange={(event) => {
                          setCountryIso(event.target.value);
                          setError("");
                        }}
                        autoComplete="country"
                        aria-describedby="phone-help"
                        aria-invalid={Boolean(error)}
                      >
                        <optgroup label="Current team">
                          {PHONE_COUNTRIES.filter(
                            (candidate) => candidate.priority,
                          ).map((candidate) => (
                            <option key={candidate.iso} value={candidate.iso}>
                              {candidate.name} (+{candidate.dialCode})
                            </option>
                          ))}
                        </optgroup>
                        <optgroup label="More regions">
                          {PHONE_COUNTRIES.filter(
                            (candidate) => !candidate.priority,
                          )
                            .toSorted((a, b) => a.name.localeCompare(b.name))
                            .map((candidate) => (
                              <option key={candidate.iso} value={candidate.iso}>
                                {candidate.name} (+{candidate.dialCode})
                              </option>
                            ))}
                        </optgroup>
                      </select>
                    </div>
                    <div className="field phone-number-field">
                      <label htmlFor="national-number">Mobile number</label>
                      <div className="national-number-control">
                        <span aria-hidden="true">+{country.dialCode}</span>
                        <input
                          id="national-number"
                          className={`input ${error ? "input-error" : ""}`}
                          type="tel"
                          inputMode="tel"
                          value={nationalNumber}
                          onChange={(event) => {
                            setNationalNumber(event.target.value);
                            if (error) setError("");
                          }}
                          autoComplete="tel-national"
                          placeholder={country.example}
                          aria-describedby={
                            error ? "phone-help login-error" : "phone-help"
                          }
                          aria-invalid={Boolean(error)}
                        />
                      </div>
                    </div>
                  </div>
                  <span className="field-help" id="phone-help">
                    Select the country code, then enter the rest of the number.
                    A leading zero is fine.
                  </span>
                  {error && (
                    <span className="field-error" id="login-error" role="alert">
                      {error}
                    </span>
                  )}
                </fieldset>
                <button
                  className="button button-primary auth-submit"
                  type="submit"
                  disabled={pending !== null}
                >
                  {pending === "request"
                    ? "Checking number…"
                    : "Continue securely"}
                </button>
              </form>
              <p className="auth-privacy-note">
                We check this number against approved mappings before the next
                sign-in step.
              </p>
            </>
          ) : step === "pin" ? (
            <>
              <button
                className="button button-ghost button-small auth-back"
                onClick={changeNumber}
                disabled={pending !== null}
              >
                <span aria-hidden="true">←</span> Change number
              </button>
              <span className="auth-kicker">
                {delivery === "web_pin"
                  ? "Temporary web access"
                  : "WhatsApp verification"}
              </span>
              <h1>
                {delivery === "web_pin"
                  ? "Enter your web PIN."
                  : "Check WhatsApp."}
              </h1>
              <p>
                {delivery === "web_pin"
                  ? "WhatsApp sign-in is paused. Use the six-digit temporary PIN shared with the project team; no WhatsApp message was sent."
                  : `We sent a six-digit code to your approved number. It expires in ${Math.max(1, Math.ceil(expiresInSeconds / 60))} minutes.`}
              </p>
              <div className="auth-phone-chip">
                <span aria-hidden="true">✓</span>
                <span>
                  Entered number
                  <strong>{maskedPhone(requestedPhone, country)}</strong>
                </span>
              </div>
              <form onSubmit={verify} noValidate>
                <div className="field pin-field">
                  <label htmlFor="web-pin">
                    {delivery === "web_pin"
                      ? "Six-digit web PIN"
                      : "Six-digit WhatsApp code"}
                  </label>
                  <input
                    ref={pinRef}
                    id="web-pin"
                    className={`input pin-input ${error ? "input-error" : ""}`}
                    type={delivery === "web_pin" ? "password" : "text"}
                    inputMode="numeric"
                    autoComplete={
                      delivery === "web_pin"
                        ? "current-password"
                        : "one-time-code"
                    }
                    pattern="[0-9]{6}"
                    maxLength={6}
                    value={pin}
                    onChange={(event) => {
                      setPin(event.target.value.replace(/\D/g, "").slice(0, 6));
                      if (error) setError("");
                    }}
                    aria-describedby={error ? "pin-help pin-error" : "pin-help"}
                    aria-invalid={Boolean(error)}
                  />
                  <span className="field-help" id="pin-help">
                    {delivery === "web_pin"
                      ? "The PIN contains six numbers and is shared separately with approved team members."
                      : "Enter the code from the WhatsApp message."}
                  </span>
                  {error && (
                    <span className="field-error" id="pin-error" role="alert">
                      {error}
                    </span>
                  )}
                </div>
                <button
                  className="button button-primary auth-submit"
                  type="submit"
                  disabled={pending !== null || pin.length !== 6}
                >
                  {pending === "verify"
                    ? "Signing in…"
                    : delivery === "web_pin"
                      ? "Sign in"
                      : "Verify and sign in"}
                </button>
              </form>
              <p className="auth-privacy-note">
                Having trouble? Check the selected country and the mobile number
                before trying again.
              </p>
            </>
          ) : step === "session" ? (
            <>
              <span className="auth-kicker">Signed in</span>
              <h1>Open your workspace.</h1>
              <p>
                Your PIN has already been accepted. We are resolving your
                workspace access now; you will not need to enter it again.
              </p>
              {error && (
                <div className="field-error" role="alert">
                  {error}
                </div>
              )}
              <button
                className="button button-primary auth-submit"
                type="button"
                disabled={pending !== null}
                onClick={() => void retrySession()}
              >
                {pending === "session"
                  ? "Opening workspace…"
                  : "Try opening workspace again"}
              </button>
              <p className="auth-privacy-note">
                If access remains unavailable, wait a moment and try this step
                again. Your completed sign-in is not repeated.
              </p>
            </>
          ) : (
            <>
              <span className="auth-kicker">Signed in · Access required</span>
              <h1>{ACCESS_DENIED_COPY[deniedSurface].heading}</h1>
              <p>{ACCESS_DENIED_COPY[deniedSurface].description}</p>
              <div className="auth-notice" role="status">
                <span aria-hidden="true">✓</span>
                <div>
                  <strong>Your session is active</strong>
                  <span>
                    We kept your completed sign-in and will not ask for the PIN
                    again while checking access.
                  </span>
                </div>
              </div>
              {error && (
                <div className="field-error" role="alert">
                  {error}
                </div>
              )}
              <button
                className="button button-primary auth-submit"
                type="button"
                disabled={pending !== null}
                onClick={() => void retrySession()}
              >
                {pending === "session"
                  ? "Checking access…"
                  : "Check access again"}
              </button>
              <p className="auth-privacy-note">
                {ACCESS_DENIED_COPY[deniedSurface].guidance}
              </p>
            </>
          )}

          {demoFallbackEnabled && (
            <div className="demo-box">
              <strong>Local demo access</strong>
              <div>
                Seeded API identities (local PIN <strong>246810</strong>): owner
                +2348012345678 · operator +2348099990001 · researcher
                +2348099990002 · superadmin +2348099990000. Or preview a role
                without live data:
              </div>
              <div className="demo-actions">
                <button
                  className="button button-secondary button-small"
                  onClick={() => enter("owner")}
                >
                  SME owner
                </button>
                <button
                  className="button button-secondary button-small"
                  onClick={() => enter("operator")}
                >
                  Operator
                </button>
                <button
                  className="button button-secondary button-small"
                  onClick={() => enter("researcher")}
                >
                  Researcher
                </button>
                <button
                  className="button button-secondary button-small"
                  onClick={() => enter("superadmin")}
                >
                  Superadmin
                </button>
              </div>
            </div>
          )}
          <p className="auth-legal">
            By continuing, you agree to our{" "}
            <Link href="/terms" className="table-action">
              Terms
            </Link>{" "}
            and{" "}
            <Link href="/privacy" className="table-action">
              Privacy notice
            </Link>
            .
          </p>
        </div>
      </section>
    </main>
  );
}
