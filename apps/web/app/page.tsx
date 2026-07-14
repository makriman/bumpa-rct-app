import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import { AppIcon } from "@/components/app-icon";
import { PublicShell } from "@/components/public-shell";
import { CSP_NONCE_REQUEST_HEADER } from "@/lib/content-security-policy";
import { buildStructuredData, publicPageMetadata } from "@/lib/site-metadata";

export const metadata: Metadata = publicPageMetadata({ path: "/" });

export default async function HomePage() {
  const structuredData = buildStructuredData();
  const nonce = (await headers()).get(CSP_NONCE_REQUEST_HEADER) ?? undefined;
  return (
    <PublicShell>
      <script
        type="application/ld+json"
        nonce={nonce}
        // Browsers intentionally hide nonce attribute values after parsing,
        // while React still holds the server value during hydration.
        suppressHydrationWarning
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(structuredData).replace(/</g, "\\u003c"),
        }}
      />
      <section className="hero">
        <div>
          <span className="eyebrow">Built for ambitious SMEs</span>
          <h1>
            Know your business. <em>Move with confidence.</em>
          </h1>
          <p className="hero-copy">
            Bumpa Bestie turns your everyday Bumpa data into clear answers and
            practical next steps — right inside WhatsApp or on the web.
          </p>
          <div className="hero-actions">
            <Link className="button button-primary" href="/login">
              Talk to your Bestie <AppIcon name="external" size={15} />
            </Link>
            <a className="button button-secondary" href="#how-it-works">
              See how it works
            </a>
          </div>
          <div className="trust-row">
            <span>Private to your business</span>
            <span>No spreadsheets needed</span>
            <span>Answers in plain language</span>
          </div>
        </div>
        <div
          className="chat-preview"
          aria-label="Preview of a Bumpa Bestie conversation"
        >
          <div className="chat-window">
            <div className="preview-top">
              <div className="preview-person">
                <span className="avatar">BB</span>
                <div>
                  Bumpa Bestie
                  <div className="online">● Your data is fresh</div>
                </div>
              </div>
              <span>•••</span>
            </div>
            <div className="bubble bubble-user">What sold best this week?</div>
            <div className="bubble bubble-agent">
              Your <strong>Adire Table Runner</strong> led sales with 24 units —
              41% more than last week.
              <div className="insight-mini">
                <span>Revenue from this product</span>
                <strong>₦456,000</strong>
                <span className="trend-up">↑ 18% vs last week</span>
              </div>
            </div>
            <div className="bubble bubble-agent">
              You have 9 left in stock. At this pace, consider reordering by
              Tuesday.
            </div>
          </div>
        </div>
      </section>
      <section className="section section-soft" id="how-it-works">
        <div>
          <div className="section-heading">
            <span className="eyebrow">Clear, useful, yours</span>
            <h2>Business intelligence without the busywork.</h2>
            <p>
              Ask the way you naturally speak. Your Bestie does the hard work of
              finding the signal in your store data.
            </p>
          </div>
          <div className="feature-grid">
            <article className="feature-card">
              <span className="feature-icon">
                <AppIcon name="external" size={22} />
              </span>
              <h3>See what is really selling</h3>
              <p>
                Find your strongest products, spot slow movers, and understand
                revenue changes without building a report.
              </p>
            </article>
            <article className="feature-card">
              <span className="feature-icon">
                <AppIcon name="sparkles" size={22} />
              </span>
              <h3>Get practical next steps</h3>
              <p>
                Turn numbers into actions — from what to restock to which
                customers deserve your attention.
              </p>
            </article>
            <article className="feature-card">
              <span className="feature-icon">
                <AppIcon name="chat" size={22} />
              </span>
              <h3>Ask from WhatsApp</h3>
              <p>
                Use a channel your team already knows, or keep longer
                conversations organised in the web workspace.
              </p>
            </article>
          </div>
        </div>
      </section>
      <section className="section">
        <div className="section-heading">
          <span className="eyebrow">How it works</span>
          <h2>From connected to confident in three steps.</h2>
        </div>
        <div className="steps">
          <article className="step">
            <h3>We connect your store</h3>
            <p>
              An approved Bumpa Bestie operator securely connects your Bumpa
              business. Your credentials are never shown to the assistant.
            </p>
          </article>
          <article className="step">
            <h3>Your team gets access</h3>
            <p>
              Approved teammates sign in with their WhatsApp number and a
              one-time code. Each person only sees your workspace.
            </p>
          </article>
          <article className="step">
            <h3>You ask. Bestie helps.</h3>
            <p>
              Get answers backed by your latest synced business data, with
              freshness and availability shown clearly.
            </p>
          </article>
        </div>
      </section>
      <section className="section">
        <div className="cta-band">
          <div>
            <h2>Your next good decision could start with one question.</h2>
            <p>Sign in to your workspace or ask your store owner for access.</p>
          </div>
          <Link className="button button-secondary" href="/login">
            Open Bumpa Bestie →
          </Link>
        </div>
      </section>
    </PublicShell>
  );
}
