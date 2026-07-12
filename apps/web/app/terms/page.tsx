import { PublicShell } from "@/components/public-shell";
export default function TermsPage() {
  return (
    <PublicShell>
      <article className="legal-wrap">
        <span className="eyebrow">Legal</span>
        <h1>Terms of use</h1>
        <p className="updated">Effective 12 July 2026</p>
        <p>
          These preview terms explain the intended product boundaries. Final
          production terms require legal approval.
        </p>
        <h2>Business advice</h2>
        <p>
          Bumpa Bestie provides decision support, not professional legal, tax,
          or financial advice. Users remain responsible for business decisions
          and should verify critical information.
        </p>
        <h2>Authorised access</h2>
        <p>
          You may access only the workspace and data you are authorised to use.
          Do not attempt to discover another tenant’s information, bypass
          permissions, or expose credentials.
        </p>
        <h2>Data availability</h2>
        <p>
          Answers depend on upstream services and the freshness of synced Bumpa
          data. The product will show known availability limits rather than
          treating missing metrics as zero.
        </p>
        <h2>Acceptable use</h2>
        <p>
          Do not use the service for unlawful activity, harassment, malware,
          credential theft, or unsafe automated writes. Connected tools are
          read-only by default and writes require confirmation.
        </p>
        <h2>Suspension</h2>
        <p>
          Access may be suspended to protect users, investigate abuse, or
          respond to a security concern. Material administrative actions are
          audit logged.
        </p>
      </article>
    </PublicShell>
  );
}
