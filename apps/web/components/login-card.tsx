import {
  ArrowLeftIcon,
  ArrowUpRightIcon,
  CheckCircleIcon,
} from "@phosphor-icons/react";
import Link from "next/link";
import type { FormEvent, RefObject } from "react";
import CountryCodeSelect from "@/components/country-code-select";
import { Brand } from "@/components/ui";
import { maskedPhone, type PhoneCountry } from "@/lib/phone";

export type LoginStep = "phone" | "pin" | "session" | "unauthorized";
export type PendingAction = "request" | "verify" | "session" | null;
export type LoginDelivery = "web_pin" | "whatsapp";

export type LoginState = {
  step: LoginStep;
  countryIso: string;
  nationalNumber: string;
  requestedPhone: string;
  delivery: LoginDelivery | null;
  expiresInSeconds: number;
  pin: string;
  error: string;
  pending: PendingAction;
};

export type LoginAction = {
  type: "patch";
  value: Partial<LoginState>;
};

export function LoginCard({
  country,
  dispatch,
  onChangeNumber,
  onRequestAccess,
  onRetrySession,
  onVerify,
  phoneCountries,
  pinRef,
  interactive,
  state,
}: {
  country: PhoneCountry;
  dispatch: React.Dispatch<LoginAction>;
  onChangeNumber: () => void;
  onRequestAccess: (event?: FormEvent<HTMLFormElement>) => Promise<void>;
  onRetrySession: () => Promise<void>;
  onVerify: (event?: FormEvent) => Promise<void>;
  phoneCountries: readonly PhoneCountry[];
  pinRef: RefObject<HTMLInputElement>;
  interactive: boolean;
  state: LoginState;
}) {
  const {
    countryIso,
    delivery,
    error,
    expiresInSeconds,
    nationalNumber,
    pending,
    pin,
    requestedPhone,
    step,
  } = state;

  return (
    <div className="auth-card">
      <Brand className="auth-brand" />
      {step === "phone" ? (
        <>
          <span className="auth-kicker">Temporary web access</span>
          <h1>Welcome back.</h1>
          <p>
            Sign in with the mobile number approved for your Bumpa Bestie
            workspace. Only mapped team members can continue.
          </p>
          <div className="auth-notice" role="note">
            <ArrowUpRightIcon aria-hidden="true" />
            <div>
              <strong>Pilot access</strong>
              <span>
                If WhatsApp verification is paused for your workspace, you will
                be prompted for the temporary six-digit web PIN.
              </span>
            </div>
          </div>
          <form onSubmit={onRequestAccess} noValidate>
            <fieldset className="phone-fieldset" disabled={!interactive}>
              <legend>Approved mobile number</legend>
              <div className="phone-fields">
                <div className="field phone-number-field">
                  <label htmlFor="national-number">Mobile number</label>
                  <div
                    className={`phone-input-control ${error ? "input-error" : ""}`}
                  >
                    <CountryCodeSelect
                      countries={phoneCountries}
                      value={countryIso}
                      onChange={(iso) =>
                        dispatch({
                          type: "patch",
                          value: { countryIso: iso, error: "" },
                        })
                      }
                      describedBy={
                        error ? "phone-help login-error" : "phone-help"
                      }
                    />
                    <input
                      id="national-number"
                      name="nationalNumber"
                      className={`input ${error ? "input-error" : ""}`}
                      type="tel"
                      inputMode="tel"
                      value={nationalNumber}
                      onChange={(event) =>
                        dispatch({
                          type: "patch",
                          value: {
                            nationalNumber: event.target.value,
                            error: "",
                          },
                        })
                      }
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
                Choose the country code, then enter the mobile number. A leading
                zero is fine.
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
              disabled={!interactive || pending !== null}
            >
              {pending === "request" ? "Checking number…" : "Continue securely"}
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
            type="button"
            onClick={onChangeNumber}
            disabled={pending !== null}
          >
            <ArrowLeftIcon aria-hidden="true" /> Change number
          </button>
          <span className="auth-kicker">
            {delivery === "web_pin"
              ? "Temporary web access"
              : "WhatsApp verification"}
          </span>
          <h1>
            {delivery === "web_pin" ? "Enter your web PIN." : "Check WhatsApp."}
          </h1>
          <p>
            {delivery === "web_pin"
              ? "WhatsApp sign-in is paused. Use the six-digit temporary PIN shared with the project team; no WhatsApp message was sent."
              : `We sent a six-digit code to your approved number. It expires in ${Math.max(1, Math.ceil(expiresInSeconds / 60))} minutes.`}
          </p>
          <div className="auth-phone-chip">
            <CheckCircleIcon aria-hidden="true" />
            <span>
              Entered number
              <strong>{maskedPhone(requestedPhone, country)}</strong>
            </span>
          </div>
          <form onSubmit={onVerify} noValidate>
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
                  delivery === "web_pin" ? "current-password" : "one-time-code"
                }
                pattern="[0-9]{6}"
                maxLength={6}
                value={pin}
                onChange={(event) =>
                  dispatch({
                    type: "patch",
                    value: {
                      pin: event.target.value.replace(/\D/g, "").slice(0, 6),
                      error: "",
                    },
                  })
                }
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
      ) : (
        <SessionAccessState
          error={error}
          pending={pending}
          retry={onRetrySession}
          unauthorized={step === "unauthorized"}
        />
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
  );
}

function SessionAccessState({
  error,
  pending,
  retry,
  unauthorized,
}: {
  error: string;
  pending: PendingAction;
  retry: () => Promise<void>;
  unauthorized: boolean;
}) {
  return (
    <>
      <span className="auth-kicker">
        {unauthorized ? "Signed in · Access required" : "Signed in"}
      </span>
      <h1>
        {unauthorized
          ? "You don’t have workspace access."
          : "Open your workspace."}
      </h1>
      <p>
        {unauthorized
          ? "Your sign-in succeeded, but this account does not have an active Bumpa Bestie workspace membership."
          : "Your PIN has already been accepted. We are resolving your workspace access now; you will not need to enter it again."}
      </p>
      {unauthorized && (
        <div className="auth-notice" role="status">
          <CheckCircleIcon aria-hidden="true" />
          <div>
            <strong>Your session is active</strong>
            <span>
              We kept your completed sign-in and will not ask for the PIN again
              while checking access.
            </span>
          </div>
        </div>
      )}
      {error && (
        <div className="field-error" role="alert">
          {error}
        </div>
      )}
      <button
        className="button button-primary auth-submit"
        type="button"
        disabled={pending !== null}
        onClick={() => void retry()}
      >
        {pending === "session"
          ? unauthorized
            ? "Checking access…"
            : "Opening workspace…"
          : unauthorized
            ? "Check access again"
            : "Try opening workspace again"}
      </button>
      <p className="auth-privacy-note">
        {unauthorized
          ? "Ask a workspace owner to activate your membership, then check access again."
          : "If access remains unavailable, wait a moment and try this step again. Your completed sign-in is not repeated."}
      </p>
    </>
  );
}
