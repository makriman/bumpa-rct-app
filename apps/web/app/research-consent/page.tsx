"use client";
import { useState } from "react";
import { PublicShell } from "@/components/public-shell";
import { Toast } from "@/components/ui";
export default function ConsentPage() {
  const [checks, setChecks] = useState([false, false]);
  const [toast, setToast] = useState("");
  const accepted = checks.every(Boolean);
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
        <div className="consent-card">
          <h2>What participation means</h2>
          <p>
            Researchers may analyse pseudonymised questions, classifications,
            channels, response timings, and outcome signals. Raw chats remain
            hidden by default and require a separate permission.
          </p>
          <div className="check-row">
            <input
              id="understand"
              type="checkbox"
              checked={checks[0]}
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
            disabled={!accepted}
            onClick={() => setToast("Research consent recorded for this demo.")}
          >
            I agree to participate
          </button>
          <button
            className="button button-secondary"
            onClick={() =>
              setToast("Your choice to decline has been recorded.")
            }
          >
            Continue without research
          </button>
        </div>
        <h2>Questions or withdrawal</h2>
        <p>
          Contact the study team through your Bumpa Bestie operator. Withdrawing
          stops future research processing; retention of previously anonymised
          results follows the final approved study protocol.
        </p>
      </div>
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </PublicShell>
  );
}
