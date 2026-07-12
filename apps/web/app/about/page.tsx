import { PublicShell } from "@/components/public-shell";

export default function AboutPage() {
  return (
    <PublicShell>
      <div className="legal-wrap">
        <span className="eyebrow">About us</span>
        <h1>A better thinking partner for small businesses.</h1>
        <p className="hero-copy">
          Bumpa Bestie is a research-instrumented AI assistant created to help
          SMEs make stronger decisions from the commerce data they already own.
        </p>
        <h2>Why we built it</h2>
        <p>
          Small business owners generate useful information every day, but
          dashboards can still leave the most important question unanswered:
          what should I do next? Bumpa Bestie makes analysis conversational,
          specific, and practical.
        </p>
        <h2>How it is different</h2>
        <p>
          It is not a generic chatbot. Every approved business has a private
          workspace and an isolated assistant profile. Answers use only
          tenant-scoped context supplied by the control plane, and the assistant
          says plainly when data is stale or unavailable.
        </p>
        <h2>Research with respect</h2>
        <p>
          The project also studies how SMEs use AI in real decisions. Research
          views are redacted by default, raw visibility is tightly permissioned,
          and consent is a continuing choice — not a one-time trap.
        </p>
      </div>
    </PublicShell>
  );
}
