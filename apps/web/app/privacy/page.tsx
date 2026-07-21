import type { Metadata } from "next";
import { PublicShell } from "@/components/public-shell";
import { publicPageMetadata } from "@/lib/site-metadata";

export const metadata: Metadata = publicPageMetadata({
  path: "/privacy",
  pageTitle: "Privacy notice",
  pageDescription:
    "How Bumpa Bestie processes, protects, and governs your business data.",
});

export default function PrivacyPage() {
  return (
    <PublicShell>
      <article className="legal-wrap">
        <span className="eyebrow">Legal</span>
        <h1>Privacy notice</h1>
        <p className="updated">Effective 12 July 2026</p>
        <p>
          This product is designed around data minimisation, tenant isolation,
          and clear access controls. This preview describes the intended
          operating model and must be reviewed by counsel before production
          launch.
        </p>
        <h2>Information we process</h2>
        <p>
          We process account details, approved WhatsApp identifiers, chat
          content, Bumpa commerce summaries, and system activity. Raw order and
          message payloads are sensitive and protected with stricter access.
        </p>
        <h2>How information is used</h2>
        <ul>
          <li>
            To authenticate users and route requests to the correct business.
          </li>
          <li>To answer business questions using authorised data.</li>
          <li>To operate, secure, and improve the service.</li>
        </ul>
        <h2>Your choices</h2>
        <p>
          You may ask your workspace owner to update access or opt out of
          WhatsApp messages using STOP.
        </p>
        <h2>Security</h2>
        <p>
          Secrets are never displayed after creation. Tenant-owned requests are
          scoped and audited. No security measure is absolute; incidents will
          follow the published operating runbook.
        </p>
      </article>
    </PublicShell>
  );
}
