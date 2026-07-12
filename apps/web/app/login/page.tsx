"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { Brand } from "@/components/ui";
import { homeForRole, safeNextPath } from "@/lib/navigation";
import type { Role } from "@/lib/demo-data";
import { apiRequest, demoFallbackEnabled } from "@/lib/api";

export default function LoginPage() {
  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [phone, setPhone] = useState(
    demoFallbackEnabled ? "+2348012345678" : "",
  );
  const [digits, setDigits] = useState(["", "", "", "", "", ""]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [devCode, setDevCode] = useState<string | null>(null);
  const refs = useRef<Array<HTMLInputElement | null>>([]);
  const requestOtp = async () => {
    if (phone.replace(/\D/g, "").length < 10) {
      setError("Enter a valid phone number including country code.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest<{ dev_code?: string | null }>(
        "/auth/request-otp",
        {
          method: "POST",
          body: JSON.stringify({ phone_e164: phone }),
        },
      );
      setDevCode(result.dev_code ?? null);
      setDigits(["", "", "", "", "", ""]);
      setStep("otp");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not send the code.",
      );
    } finally {
      setBusy(false);
    }
  };
  const enter = (role: Role) => {
    document.cookie = "bb_session=demo; path=/; SameSite=Lax";
    document.cookie = `bb_demo_role=${role}; path=/; SameSite=Lax`;
    const params = new URLSearchParams(window.location.search);
    window.location.href =
      safeNextPath(params.get("next")) ?? homeForRole(role);
  };
  const verify = async () => {
    setBusy(true);
    setError("");
    try {
      await apiRequest("/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone_e164: phone, code: digits.join("") }),
      });
      const me = await apiRequest<{ platform_roles: string[] }>("/auth/me");
      const role: Role = me.platform_roles.includes("superadmin")
        ? "superadmin"
        : me.platform_roles.includes("operator")
          ? "operator"
          : me.platform_roles.includes("researcher")
            ? "researcher"
            : "owner";
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.href = safeNextPath(next) ?? homeForRole(role);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "That code could not be verified.",
      );
    } finally {
      setBusy(false);
    }
  };
  const updateDigit = (index: number, value: string) => {
    const digit = value.replace(/\D/g, "").slice(-1);
    const next = [...digits];
    next[index] = digit;
    setDigits(next);
    if (digit && index < 5) refs.current[index + 1]?.focus();
  };
  return (
    <main className="auth-page" id="main-content">
      <section className="auth-art">
        <Brand />
        <blockquote className="auth-quote">
          “I stopped guessing which products needed my attention. Now I ask, and
          I know.”<small>— Early pilot SME owner, Lagos</small>
        </blockquote>
        <div style={{ color: "#9bb9ac", fontSize: 13 }}>
          Private workspace · Tenant-isolated data · Clear freshness
        </div>
      </section>
      <section className="auth-main">
        <div className="auth-card">
          <Link href="/" className="brand" style={{ marginBottom: 30 }}>
            <span className="brand-mark">B</span>
            <span className="brand-word">Bumpa Bestie</span>
          </Link>
          {step === "phone" ? (
            <>
              <h1>Welcome back.</h1>
              <p>
                Enter your approved WhatsApp number. We will send a short
                one-time code to confirm it is you.
              </p>
              <div className="field">
                <label htmlFor="phone">WhatsApp phone number</label>
                <input
                  id="phone"
                  className={`input ${error ? "input-error" : ""}`}
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !busy) {
                      event.preventDefault();
                      void requestOtp();
                    }
                  }}
                  autoComplete="tel"
                  aria-describedby={error ? "login-error" : "phone-help"}
                />
                <span className="field-help" id="phone-help">
                  Include your country code, for example +234.
                </span>
                {error && (
                  <span className="field-error" id="login-error" role="alert">
                    {error}
                  </span>
                )}
              </div>
              <button
                className="button button-primary"
                style={{ width: "100%", marginTop: 22 }}
                onClick={requestOtp}
                disabled={busy}
              >
                {busy ? "Sending code…" : "Send WhatsApp code"}
              </button>
            </>
          ) : (
            <>
              <button
                className="button button-ghost button-small"
                onClick={() => {
                  setStep("phone");
                  setError("");
                }}
              >
                ← Change number
              </button>
              <h1>Check WhatsApp.</h1>
              <p>
                We sent a six-digit code to <strong>{phone}</strong>. It expires
                in 10 minutes.
                {devCode && (
                  <>
                    <br />
                    Local API code: <strong>{devCode}</strong>
                  </>
                )}
              </p>
              <div
                className="otp-inputs"
                onPaste={(e) => {
                  e.preventDefault();
                  const pasted = e.clipboardData
                    .getData("text")
                    .replace(/\D/g, "")
                    .slice(0, 6)
                    .split("");
                  setDigits([...pasted, ...Array(6 - pasted.length).fill("")]);
                }}
              >
                {digits.map((digit, index) => (
                  <input
                    key={index}
                    ref={(node) => {
                      refs.current[index] = node;
                    }}
                    className="otp-box"
                    aria-label={`Digit ${index + 1}`}
                    inputMode="numeric"
                    maxLength={1}
                    value={digit}
                    onChange={(e) => updateDigit(index, e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Backspace" && !digit && index > 0)
                        refs.current[index - 1]?.focus();
                    }}
                  />
                ))}
              </div>
              {error && (
                <div className="alert alert-danger" role="alert">
                  {error}
                </div>
              )}
              <button
                className="button button-primary"
                style={{ width: "100%" }}
                onClick={verify}
                disabled={busy || digits.some((d) => !d)}
              >
                {busy ? "Checking…" : "Verify and sign in"}
              </button>
              <button
                className="button button-ghost"
                style={{ width: "100%", marginTop: 8 }}
                onClick={() => void requestOtp()}
                disabled={busy}
              >
                {busy ? "Sending new code…" : "Resend code"}
              </button>
            </>
          )}
          {demoFallbackEnabled && (
            <div className="demo-box">
              <strong>Local demo access</strong>
              <div>
                Seeded API identities (OTP <strong>246810</strong>): owner
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
          <p style={{ fontSize: 12, textAlign: "center", marginTop: 22 }}>
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
