"use client";

import { useEffect } from "react";
import { emitStructuredWebEvent } from "@bumpabestie/web-foundation";

export default function ConsumerError({ reset }: { reset: () => void }) {
  useEffect(() => {
    emitStructuredWebEvent({
      event: "frontend_error_boundary",
      surface: "consumer",
      status: "error",
    });
  }, []);
  return (
    <main className="public-main">
      <section className="card empty-state" role="alert">
        <div className="empty-inner">
          <h1>Something interrupted this page</h1>
          <p>Your conversation is still safe. Try loading the page again.</p>
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
