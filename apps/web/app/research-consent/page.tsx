"use client";
import { useState } from "react";
import { PublicShell } from "@/components/public-shell";
import { apiRequest } from "@/lib/api";

const RESEARCH_POLICY_VERSION = "v1";
type ConsentChoice = "granted" | "withdrawn";

export default function ConsentPage() {
  const [checks, setChecks] = useState([false, false]);
  const [submitting, setSubmitting] = useState<ConsentChoice | null>(null);
  const [feedback, setFeedback] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);
  const accepted = checks.every(Boolean);

  const recordChoice = async (choice: ConsentChoice) => {
    setSubmitting(choice);
    setFeedback(null);
    try {
      const result = await apiRequest<{ status: ConsentChoice }>(
        "/tenants/current/research-consent",
        {
          method: "POST",
          body: JSON.stringify({
            status: choice,
            policy_version: RESEARCH_POLICY_VERSION,
          }),
        },
      );
      if (result.status !== choice) {
        throw new Error("The saved consent choice did not match your request.");
      }
      setFeedback({
        kind: "success",
        message:
          choice === "granted"
            ? "Your research participation consent has been saved."
            : "Your choice to continue without research participation has been saved.",
      });
    } catch (reason) {
      setFeedback({
        kind: "error",
        message:
          reason instanceof Error
            ? reason.message
            : "Your research choice could not be saved. Please try again.",
      });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <PublicShell>
      <div className="legal-wrap">
        <span className="eyebrow">Research participation</span>
        <h1>Your data. Your choice.</h1>
        <p className="hero-copy">
          Bumpa Bestie studies how SMEs use AI to understand their businesses.
          Participation is voluntary, and declining does not remove your access
          to the product.
        </p>
        <div className="consent-card" aria-busy={submitting !== null}>
          <h2>What participation means</h2>
          <p>
            Researchers may analyse pseudonymised questions, classifications,
            channels, response timings, and outcome signals. Default research
            exports are redacted. Access to raw chat content is restricted,
            reason-gated, and audited.
          </p>
          <div className="check-row">
            <input
              id="understand"
              type="checkbox"
              checked={checks[0]}
              disabled={submitting !== null}
              onChange={(e) => setChecks([e.target.checked, checks[1]])}
            />
            <label htmlFor="understand">
              I understand what data is included and that research exports are
              redacted by default.
            </label>
          </div>
          <div className="check-row">
            <input
              id="voluntary"
              type="checkbox"
              checked={checks[1]}
              disabled={submitting !== null}
              onChange={(e) => setChecks([checks[0], e.target.checked])}
            />
            <label htmlFor="voluntary">
              I understand participation is voluntary and I can withdraw consent
              later.
            </label>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button
            className="button button-primary"
            disabled={!accepted || submitting !== null}
            onClick={() => void recordChoice("granted")}
          >
            {submitting === "granted"
              ? "Saving agreement…"
              : "I agree to participate"}
          </button>
          <button
            className="button button-secondary"
            disabled={submitting !== null}
            onClick={() => void recordChoice("withdrawn")}
          >
            {submitting === "withdrawn"
              ? "Saving choice…"
              : "Continue without research"}
          </button>
        </div>
        {feedback && (
          <div
            className={`alert ${feedback.kind === "success" ? "alert-success" : "alert-danger"}`}
            role={feedback.kind === "error" ? "alert" : "status"}
            aria-live={feedback.kind === "error" ? "assertive" : "polite"}
          >
            {feedback.message}
          </div>
        )}
        <p className="table-secondary">
          You must be signed in as a workspace owner or administrator to save
          this choice. Declining does not restrict normal product access.
        </p>
        <h2>Questions or withdrawal</h2>
        <p>
          Contact the study team through your Bumpa Bestie operator. Withdrawing
          stops new research classification and blocks access to tenant research
          data and generated artifacts. It does not delete operational product
          records; ask your operator about a separate privacy or deletion
          request.
        </p>
      </div>
    </PublicShell>
  );
}
