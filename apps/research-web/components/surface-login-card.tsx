import { ArrowLeftIcon, CheckCircleIcon } from "@phosphor-icons/react";
import Link from "next/link";
import type { FormEvent, RefObject } from "react";
import CountryCodeSelect from "@/components/country-code-select";
import { Brand } from "@/components/ui";
import { maskedPhone, type PhoneCountry } from "@/lib/phone";

export type SurfaceLoginState = {
  step: "phone" | "pin" | "session" | "unauthorized";
  countryIso: string;
  nationalNumber: string;
  requestedPhone: string;
  delivery: "web_pin" | "whatsapp" | null;
  expiresInSeconds: number;
  pin: string;
  error: string;
  pending: "request" | "verify" | "session" | null;
};

export type SurfaceLoginAction = {
  type: "patch";
  value: Partial<SurfaceLoginState>;
};

export function SurfaceLoginCard({
  country,
  description,
  dispatch,
  onRequestAccess,
  onRetrySession,
  onVerify,
  phoneCountries,
  pinRef,
  state,
  surfaceName,
}: {
  country: PhoneCountry;
  description: string;
  dispatch: React.Dispatch<SurfaceLoginAction>;
  onRequestAccess: (event: FormEvent) => Promise<void>;
  onRetrySession: () => Promise<void>;
  onVerify: (event: FormEvent) => Promise<void>;
  phoneCountries: readonly PhoneCountry[];
  pinRef: RefObject<HTMLInputElement>;
  state: SurfaceLoginState;
  surfaceName: string;
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
          <span className="auth-kicker">Restricted workspace</span>
          <h1>Sign in to {surfaceName}.</h1>
          <p>{description}</p>
          <form onSubmit={onRequestAccess} noValidate>
            <fieldset className="phone-fieldset">
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
                        error
                          ? "surface-phone-help surface-login-error"
                          : "surface-phone-help"
                      }
                    />
                    <input
                      id="national-number"
                      className="input"
                      type="tel"
                      inputMode="tel"
                      autoComplete="tel-national"
                      placeholder={country.example}
                      value={nationalNumber}
                      aria-describedby={
                        error
                          ? "surface-phone-help surface-login-error"
                          : "surface-phone-help"
                      }
                      aria-invalid={Boolean(error)}
                      onChange={(event) =>
                        dispatch({
                          type: "patch",
                          value: {
                            nationalNumber: event.target.value,
                            error: "",
                          },
                        })
                      }
                    />
                  </div>
                </div>
              </div>
              <span className="field-help" id="surface-phone-help">
                Use the mobile number assigned to your platform role.
              </span>
              {error && (
                <span
                  className="field-error"
                  id="surface-login-error"
                  role="alert"
                >
                  {error}
                </span>
              )}
            </fieldset>
            <button
              className="button button-primary auth-submit"
              type="submit"
              disabled={pending !== null}
            >
              {pending === "request" ? "Checking access…" : "Continue securely"}
            </button>
          </form>
        </>
      ) : step === "pin" ? (
        <>
          <button
            className="button button-ghost button-small auth-back"
            type="button"
            onClick={() =>
              dispatch({
                type: "patch",
                value: { step: "phone", error: "" },
              })
            }
          >
            <ArrowLeftIcon size={17} aria-hidden="true" /> Change number
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
            Enter the six-digit code for {maskedPhone(requestedPhone, country)}.
            It expires in {Math.max(1, Math.ceil(expiresInSeconds / 60))}{" "}
            minutes.
          </p>
          <form onSubmit={onVerify} noValidate>
            <div className="field pin-field">
              <label htmlFor="surface-pin">Six-digit code</label>
              <input
                ref={pinRef}
                id="surface-pin"
                className="input pin-input"
                type="password"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={pin}
                aria-invalid={Boolean(error)}
                onChange={(event) =>
                  dispatch({
                    type: "patch",
                    value: {
                      pin: event.target.value.replace(/\D/g, "").slice(0, 6),
                      error: "",
                    },
                  })
                }
              />
              {error && (
                <span className="field-error" role="alert">
                  {error}
                </span>
              )}
            </div>
            <button
              className="button button-primary auth-submit"
              type="submit"
              disabled={pending !== null || pin.length !== 6}
            >
              {pending === "verify" ? "Signing in…" : "Verify and sign in"}
            </button>
          </form>
        </>
      ) : (
        <SurfaceSessionState
          error={error}
          pending={pending}
          retry={onRetrySession}
          surfaceName={surfaceName}
          unauthorized={step === "unauthorized"}
        />
      )}
      <p className="auth-legal">
        Access is logged and subject to the platform security policy.{" "}
        <Link href="/">Return to this workspace</Link>.
      </p>
    </div>
  );
}

function SurfaceSessionState({
  error,
  pending,
  retry,
  surfaceName,
  unauthorized,
}: {
  error: string;
  pending: SurfaceLoginState["pending"];
  retry: () => Promise<void>;
  surfaceName: string;
  unauthorized: boolean;
}) {
  return (
    <>
      <span className="auth-kicker">
        {unauthorized ? "Access required" : "Signed in"}
      </span>
      <h1>
        {unauthorized
          ? "Your account cannot open this workspace."
          : `Opening ${surfaceName}.`}
      </h1>
      <p>
        {unauthorized
          ? "Your sign-in succeeded, but the required platform role is not active."
          : "Your identity is verified. We are checking the platform role attached to this host."}
      </p>
      {unauthorized && (
        <div className="auth-notice" role="status">
          <CheckCircleIcon size={20} aria-hidden="true" />
          <div>
            <strong>Your session is active</strong>
            <span>Ask an authorised platform owner to review your role.</span>
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
            : "Try again"}
      </button>
    </>
  );
}
