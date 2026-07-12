"use client";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Badge, PageHeader, Toast } from "@/components/ui";
const connectors = [
  {
    name: "Google Drive",
    icon: "△",
    desc: "Find approved files and business documents.",
  },
  {
    name: "Google Sheets",
    icon: "▦",
    desc: "Read approved spreadsheets for additional context.",
  },
  {
    name: "Gmail",
    icon: "✉",
    desc: "Search approved messages. Read-only by default.",
  },
  {
    name: "Google Calendar",
    icon: "□",
    desc: "Read events and upcoming business commitments.",
  },
  {
    name: "Meta Ads",
    icon: "◎",
    desc: "Review campaign performance after OAuth setup.",
  },
];
export default function McpPage() {
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState("");
  return (
    <AppShell surface="user" title="Connections">
      <PageHeader
        title="Business connections"
        description="Add approved sources Bestie may read. Arbitrary servers are never accepted."
      />
      <div className="alert alert-warning">
        All connections start read-only and require operator approval. Any
        future write action will ask for confirmation and create an audit
        record.
      </div>
      <div className="grid grid-2">
        {connectors.map((c) => (
          <section className="card connection-card" key={c.name}>
            <div className="connection-icon">{c.icon}</div>
            <div className="connection-body">
              <strong>{c.name}</strong>
              <p>{c.desc}</p>
              <div style={{ marginTop: 8 }}>
                {enabled[c.name] ? (
                  <Badge tone="warning">Awaiting approval</Badge>
                ) : (
                  <Badge tone="neutral">Not connected</Badge>
                )}
              </div>
            </div>
            <button
              className={`toggle ${enabled[c.name] ? "on" : ""}`}
              role="switch"
              aria-checked={Boolean(enabled[c.name])}
              aria-label={`${enabled[c.name] ? "Disable" : "Enable"} ${c.name}`}
              onClick={() => {
                setEnabled((v) => ({ ...v, [c.name]: !v[c.name] }));
                setToast(
                  `${c.name} ${enabled[c.name] ? "disabled" : "prepared for OAuth"}.`,
                );
              }}
            />
          </section>
        ))}
      </div>
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
