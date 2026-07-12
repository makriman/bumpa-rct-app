"use client";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Badge, Modal, PageHeader, Toast } from "@/components/ui";
import { phones } from "@/lib/demo-data";
export default function WhatsAppPage() {
  const [modal, setModal] = useState(false);
  const [toast, setToast] = useState("");
  return (
    <AppShell surface="user" title="WhatsApp numbers">
      <PageHeader
        title="Approved WhatsApp numbers"
        description="Only approved numbers can ask Bestie about Kaia Home."
        actions={
          <button
            className="button button-primary"
            onClick={() => setModal(true)}
          >
            ＋ Add number
          </button>
        }
      />
      <div className="alert alert-info">
        Messages from unknown numbers are rejected safely. Send{" "}
        <strong>STOP</strong> to opt out and <strong>START</strong> to re-enable
        messages.
      </div>
      <div className="grid">
        {phones.map((p) => (
          <section className="card connection-card" key={p.number}>
            <div className="connection-icon">◌</div>
            <div className="connection-body">
              <strong>{p.label}</strong>
              <p>
                {p.number} · {p.lastSeen}
              </p>
            </div>
            <Badge>{p.status}</Badge>
            <button className="button button-ghost button-small">Manage</button>
          </section>
        ))}
      </div>
      {modal && (
        <Modal
          title="Add a WhatsApp number"
          onClose={() => setModal(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setModal(false)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                onClick={() => {
                  setModal(false);
                  setToast("Verification is ready for API delivery.");
                }}
              >
                Send verification
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="phone-label">Label</label>
            <input
              id="phone-label"
              className="input"
              placeholder="e.g. Ada · Store manager"
            />
          </div>
          <div className="field">
            <label htmlFor="new-phone">Phone number</label>
            <input
              id="new-phone"
              type="tel"
              className="input"
              placeholder="+234…"
            />
          </div>
          <p className="field-help">
            The person must verify the number before it can access business
            data.
          </p>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
