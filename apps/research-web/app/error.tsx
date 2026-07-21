"use client";

import { useEffect } from "react";
import { emitStructuredWebEvent } from "@bumpabestie/web-foundation";

export default function ResearchError({ reset }: { reset: () => void }) {
  useEffect(() => {
    emitStructuredWebEvent({
      event: "frontend_error_boundary",
      surface: "research",
      status: "error",
    });
  }, []);
  return (
    <main className="content">
      <section className="card empty-state" role="alert">
        <div className="empty-inner">
          <h1>Research page unavailable</h1>
          <p>No research artifact was changed. Retry this screen when ready.</p>
          <button
            type="button"
            className="button button-primary"
            onClick={reset}
          >
            Try again
          </button>
        </div>
      </section>
    </main>
  );
}
