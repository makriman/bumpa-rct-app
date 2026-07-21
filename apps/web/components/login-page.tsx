"use client";

import { type FormEvent, useEffect, useReducer, useRef } from "react";
import {
  LoginCard,
  type LoginAction,
  type LoginDelivery,
  type LoginState,
} from "@/components/login-card";
import { Brand } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { assembleE164, countryByIso, type PhoneCountry } from "@/lib/phone";
import { useHydrated } from "@/lib/use-hydrated";

type AccessRequestResponse = {
  delivery: LoginDelivery;
  expires_in_seconds: number;
};

type SessionResponse = {
  memberships: Array<{ role: string; status: string }>;
};

const initialState: LoginState = {
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

function loginReducer(state: LoginState, action: LoginAction): LoginState {
  return action.type === "patch" ? { ...state, ...action.value } : state;
}

export default function LoginPage({
  phoneCountries,
  nextPath,
}: {
  phoneCountries: readonly PhoneCountry[];
  nextPath: string | null;
}) {
  const [state, dispatch] = useReducer(loginReducer, initialState);
  const interactive = useHydrated();
  const pinRef = useRef<HTMLInputElement | null>(null);
  const country = countryByIso(phoneCountries, state.countryIso);

  useEffect(() => {
    if (state.step === "pin") pinRef.current?.focus();
  }, [state.step]);

  const requestAccess = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const submittedNumber =
      event?.currentTarget.elements.namedItem("nationalNumber");
    const nationalNumber =
      submittedNumber instanceof HTMLInputElement
        ? submittedNumber.value
        : state.nationalNumber;
    const phone = assembleE164(country, nationalNumber);
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
      if (result.delivery !== "web_pin" && result.delivery !== "whatsapp") {
        throw new Error("The configured sign-in method is unavailable.");
      }
      dispatch({
        type: "patch",
        value: {
          requestedPhone: phone.e164,
          delivery: result.delivery,
          expiresInSeconds: result.expires_in_seconds,
          pin: "",
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
    const me = await apiRequest<SessionResponse>("/auth/me");
    const hasWorkspace = me.memberships.some(
      (membership) =>
        membership.status === "active" &&
        ["owner", "admin", "member"].includes(membership.role),
    );
    if (!hasWorkspace) {
      dispatch({ type: "patch", value: { step: "unauthorized" } });
      return;
    }
    window.location.href = nextPath ?? "/chat";
  };

  const retrySession = async () => {
    dispatch({ type: "patch", value: { pending: "session", error: "" } });
    try {
      await resolveSession();
    } catch {
      dispatch({
        type: "patch",
        value: {
          error:
            "You are signed in, but we could not open your workspace. Try again.",
        },
      });
    } finally {
      dispatch({ type: "patch", value: { pending: null } });
    }
  };

  const verify = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!/^\d{6}$/.test(state.pin)) {
      dispatch({
        type: "patch",
        value: { error: "Enter the complete six-digit web PIN." },
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
    } catch (reason) {
      dispatch({
        type: "patch",
        value: {
          error:
            reason instanceof Error
              ? reason.message
              : "That PIN could not be verified.",
          pending: null,
        },
      });
      return;
    }

    dispatch({
      type: "patch",
      value: { step: "session", pin: "", pending: "session" },
    });
    try {
      await resolveSession();
    } catch {
      dispatch({
        type: "patch",
        value: {
          error:
            "Your PIN was accepted, but we could not open your workspace. Try again without entering the PIN again.",
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
          “I stopped guessing which products needed my attention. Now I ask, and
          I know.”<small>— Early pilot SME owner, Lagos</small>
        </blockquote>
        <div className="auth-trust-line">
          Private workspace · Tenant-isolated data · Clear freshness
        </div>
      </section>
      <section className="auth-main">
        <LoginCard
          country={country}
          dispatch={dispatch}
          onChangeNumber={() =>
            dispatch({
              type: "patch",
              value: { step: "phone", delivery: null, pin: "", error: "" },
            })
          }
          onRequestAccess={requestAccess}
          onRetrySession={retrySession}
          onVerify={verify}
          phoneCountries={phoneCountries}
          pinRef={pinRef}
          interactive={interactive}
          state={state}
        />
      </section>
    </main>
  );
}
