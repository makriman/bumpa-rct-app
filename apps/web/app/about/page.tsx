import type { Metadata } from "next";
import { PublicShell } from "@/components/public-shell";
import { publicPageMetadata } from "@/lib/site-metadata";

export const metadata: Metadata = publicPageMetadata({
  path: "/about",
  pageTitle: "About",
  pageDescription:
    "Why Bumpa Bestie gives small businesses practical answers from their own commerce data.",
});

export default function AboutPage() {
  return (
    <PublicShell>
      <div className="legal-wrap">
        <span className="eyebrow">About us</span>
        <h1>A better thinking partner for small businesses.</h1>
        <p className="hero-copy">
          Bumpa Bestie is an AI business assistant created to help SMEs make
          stronger decisions from the commerce data they already own.
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
        <h2>Built around your business</h2>
        <p>
          Your conversations stay connected to your own workspace, with clear
          data freshness and access controls. Bestie is designed to make useful
          business context easier to understand without exposing it to another
          business.
        </p>
      </div>
    </PublicShell>
  );
}
