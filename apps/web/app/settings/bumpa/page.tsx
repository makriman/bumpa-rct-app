"use client";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Badge, Card, PageHeader, Toast } from "@/components/ui";
export default function BumpaPage() {
  const [toast, setToast] = useState("");
  return (
    <AppShell surface="user" title="Bumpa connection">
      <PageHeader
        title="Bumpa data connection"
        description="See what Bestie can use and when your business data was last refreshed."
        actions={
          <button
            className="button button-secondary"
            onClick={() => setToast("Your operator has been notified.")}
          >
            Contact operator
          </button>
        }
      />
      <div className="grid grid-2">
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Connection health</h2>
              <p>Your API key is encrypted and never displayed.</p>
            </div>
            <Badge>Connected</Badge>
          </div>
          <div className="detail-list">
            <div className="detail-row">
              <span className="detail-label">Business scope</span>
              <span className="detail-value">Business · •••• 7K2A</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Last successful sync</span>
              <span className="detail-value">12 July 2026, 10:30 WAT</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Next scheduled sync</span>
              <span className="detail-value">12 July 2026, 11:30 WAT</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Data window</span>
              <span className="detail-value">
                Orders and analytics · last 12 months
              </span>
            </div>
          </div>
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Available data</h2>
              <p>Missing metrics are shown as unavailable, never as zero.</p>
            </div>
          </div>
          {[
            ["Sales analytics", "Available"],
            ["Orders", "Available"],
            ["Products", "Available"],
            ["Customers", "Available"],
            ["Gross profit", "Unavailable"],
          ].map(([name, status]) => (
            <div className="detail-row" key={name}>
              <span className="detail-value">{name}</span>
              <Badge>{status}</Badge>
            </div>
          ))}
        </Card>
      </div>
      <Card padded className="">
        <div className="card-head">
          <div>
            <h2>Recent sync activity</h2>
            <p>The latest jobs run automatically in the background.</p>
          </div>
          <button
            className="button button-secondary button-small"
            onClick={() => setToast("Sync request queued for your operator.")}
          >
            Request refresh
          </button>
        </div>
        <div className="timeline">
          <div className="timeline-item">
            <strong>Full sync completed</strong>
            <p>11 of 11 analytics datasets and 284 orders processed.</p>
            <span className="tag">12 minutes ago · 31s</span>
          </div>
          <div className="timeline-item">
            <strong>Profit metric unavailable</strong>
            <p>
              Bumpa returned a body-level availability error. No zero value was
              stored.
            </p>
            <span className="tag">12 minutes ago</span>
          </div>
          <div className="timeline-item">
            <strong>Scheduled sync completed</strong>
            <p>Canonical tables updated without errors.</p>
            <span className="tag">1 hour ago · 28s</span>
          </div>
        </div>
      </Card>
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
