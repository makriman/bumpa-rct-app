"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Badge, Filters, Modal, PageHeader, Toast } from "@/components/ui";
import { team } from "@/lib/demo-data";
import { LiveDataBanner } from "@/components/live-data-banner";
export default function TeamPage() {
  const [query, setQuery] = useState("");
  const [modal, setModal] = useState(false);
  const [toast, setToast] = useState("");
  const rows = useMemo(
    () =>
      team.filter((m) => m.name.toLowerCase().includes(query.toLowerCase())),
    [query],
  );
  return (
    <AppShell surface="user" title="Team">
      <PageHeader
        title="Team access"
        description="Invite trusted people and control what they can do in your workspace."
        actions={
          <button
            className="button button-primary"
            onClick={() => setModal(true)}
          >
            ＋ Invite teammate
          </button>
        }
      />
      <LiveDataBanner endpoint="/settings/team" label="team settings" />
      <div className="alert alert-info">
        Only owners and admins can change access. Every invite, role change, and
        removal is recorded.
      </div>
      <Filters search={query} setSearch={setQuery}>
        <select className="filter-select" aria-label="Filter by status">
          <option>All statuses</option>
          <option>Active</option>
          <option>Invited</option>
        </select>
      </Filters>
      <section className="card table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Person</th>
              <th>Contact</th>
              <th>Role</th>
              <th>Status</th>
              <th>Last active</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.name}>
                <td>
                  <div
                    style={{ display: "flex", gap: 10, alignItems: "center" }}
                  >
                    <span className="avatar">{m.initials}</span>
                    <span className="table-primary">{m.name}</span>
                  </div>
                </td>
                <td>{m.contact}</td>
                <td>{m.role}</td>
                <td>
                  <Badge>{m.status}</Badge>
                </td>
                <td>{m.lastSeen}</td>
                <td>
                  <button className="button button-ghost button-small">
                    Manage
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {modal && (
        <Modal
          title="Invite a teammate"
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
                  setToast(
                    "Invite prepared. WhatsApp delivery will activate when configured.",
                  );
                }}
              >
                Send invite
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="invite-name">Full name</label>
            <input
              id="invite-name"
              className="input"
              placeholder="e.g. Chidi Okoro"
            />
          </div>
          <div className="field">
            <label htmlFor="invite-phone">WhatsApp number</label>
            <input id="invite-phone" className="input" placeholder="+234…" />
          </div>
          <div className="field">
            <label htmlFor="invite-role">Workspace role</label>
            <select id="invite-role" className="select">
              <option>Member — can chat and view settings</option>
              <option>Admin — can also manage the team</option>
            </select>
          </div>
          <div
            className="alert alert-warning"
            style={{ marginTop: 18, marginBottom: 0 }}
          >
            Admins can invite and remove members. Only the owner can promote
            another admin.
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
