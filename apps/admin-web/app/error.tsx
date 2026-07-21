"use client";

import { useEffect } from "react";
import { emitStructuredWebEvent } from "@bumpabestie/web-foundation";

export default function AdminError({ reset }: { reset: () => void }) {
  useEffect(() => {
    emitStructuredWebEvent({
      event: "frontend_error_boundary",
      surface: "admin",
      status: "error",
    });
  }, []);
  return (
    <main className="content">
      <section className="card empty-state" role="alert">
        <div className="empty-inner">
          <h1>Operations page unavailable</h1>
          <p>No action was completed. Retry this screen when you are ready.</p>
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
