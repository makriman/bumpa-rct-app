"use client";

import { type FormEvent, useEffect, useMemo, useReducer, useRef } from "react";
import {
  SurfaceLoginCard,
  type SurfaceLoginAction,
  type SurfaceLoginState,
} from "@/components/surface-login-card";
import { Brand } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { assembleE164, countryByIso, type PhoneCountry } from "@/lib/phone";

type AccessRequestResponse = {
  delivery: "web_pin" | "whatsapp";
  expires_in_seconds: number;
};

type SessionResponse = { platform_roles: string[] };

const initialState: SurfaceLoginState = {
  step: "phone",
  countryIso: "GB",
  nationalNumber: "",
  requestedPhone: "",
  delivery: null,
  expiresInSeconds: 0,
  pin: "",
  error: "",
  pending: null,
};

function surfaceLoginReducer(
  state: SurfaceLoginState,
  action: SurfaceLoginAction,
): SurfaceLoginState {
  return action.type === "patch" ? { ...state, ...action.value } : state;
}

export function SurfaceLoginPage({
  allowedRoles,
  description,
  homePath,
  nextPath,
  phoneCountries,
  surfaceName,
}: {
  allowedRoles: readonly string[];
  description: string;
  homePath: string;
  nextPath: string | null;
  phoneCountries: readonly PhoneCountry[];
  surfaceName: string;
}) {
  const [state, dispatch] = useReducer(surfaceLoginReducer, initialState);
  const pinRef = useRef<HTMLInputElement>(null);
  const country = countryByIso(phoneCountries, state.countryIso);
  const allowedRoleSet = useMemo(() => new Set(allowedRoles), [allowedRoles]);

  useEffect(() => {
    if (state.step === "pin") pinRef.current?.focus();
  }, [state.step]);

  const requestAccess = async (event: FormEvent) => {
    event.preventDefault();
    const phone = assembleE164(country, state.nationalNumber);
    if (!phone.ok) {
      dispatch({ type: "patch", value: { error: phone.error } });
      return;
    }
    dispatch({ type: "patch", value: { pending: "request", error: "" } });
    try {
      const result = await apiRequest<AccessRequestResponse>(
        "/auth/request-otp",
        {
          method: "POST",
          body: JSON.stringify({ phone_e164: phone.e164 }),
        },
      );
      dispatch({
        type: "patch",
        value: {
          requestedPhone: phone.e164,
          delivery: result.delivery,
          expiresInSeconds: result.expires_in_seconds,
          step: "pin",
        },
      });
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "We could not check this number. Try again.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { pending: null } });
    }
  };

  const resolveSession = async () => {
    const session = await apiRequest<SessionResponse>("/auth/me");
    if (!session.platform_roles.some((role) => allowedRoleSet.has(role))) {
      dispatch({ type: "patch", value: { step: "unauthorized" } });
      return;
    }
    window.location.href = nextPath ?? homePath;
  };

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    if (!/^\d{6}$/.test(state.pin)) {
      dispatch({
        type: "patch",
        value: { error: "Enter the complete six-digit code." },
      });
      return;
    }
    dispatch({ type: "patch", value: { pending: "verify", error: "" } });
    try {
      await apiRequest("/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({
          phone_e164: state.requestedPhone,
          code: state.pin,
        }),
      });
      dispatch({
        type: "patch",
        value: { step: "session", pin: "", pending: "session" },
      });
      await resolveSession();
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "That code could not be verified.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { pending: null } });
    }
  };

  const retrySession = async () => {
    dispatch({ type: "patch", value: { pending: "session", error: "" } });
    try {
      await resolveSession();
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : `We could not open ${surfaceName}.`,
        },
      });
    } finally {
      dispatch({ type: "patch", value: { pending: null } });
    }
  };

  return (
    <main className="auth-page" id="main-content">
      <section className="auth-art">
        <Brand />
        <blockquote className="auth-quote">
          Clear access boundaries make sensitive work safer and easier to audit.
          <small>{surfaceName}</small>
        </blockquote>
        <div className="auth-trust-line">
          Role-gated access · Host-isolated session · Audit-ready actions
        </div>
      </section>
      <section className="auth-main">
        <SurfaceLoginCard
          country={country}
          description={description}
          dispatch={dispatch}
          onRequestAccess={requestAccess}
          onRetrySession={retrySession}
          onVerify={verify}
          phoneCountries={phoneCountries}
          pinRef={pinRef}
          state={state}
          surfaceName={surfaceName}
        />
      </section>
    </main>
  );
}
