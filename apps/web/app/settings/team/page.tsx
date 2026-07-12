"use client";

import { useMemo, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import {
  Badge,
  Filters,
  Modal,
  PageHeader,
  StatePanel,
  Toast,
} from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { maskPhone, titleCase, type TeamMember } from "@/lib/platform-data";
import { previewTeam } from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";

export default function TeamPage() {
  const resource = useApiResource<TeamMember[]>("/settings/team", previewTeam);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [modal, setModal] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const rows = useMemo(
    () =>
      (resource.data ?? []).filter((member) => {
        const matchesText =
          `${member.name} ${member.email ?? ""} ${member.phone_e164}`
            .toLowerCase()
            .includes(query.toLowerCase());
        return matchesText && (status === "all" || member.status === status);
      }),
    [query, resource.data, status],
  );
  const invite = async () => {
    setBusy(true);
    setError("");
    try {
      await apiRequest("/settings/team", {
        method: "POST",
        body: JSON.stringify({
          name,
          phone_e164: phone,
          email: email || null,
          role,
        }),
      });
      await resource.reload();
      setModal(false);
      setName("");
      setPhone("");
      setEmail("");
      setRole("member");
      setToast("Team member added to this workspace.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The team member could not be added.",
      );
    } finally {
      setBusy(false);
    }
  };
  const remove = async (member: TeamMember) => {
    if (!window.confirm(`Remove ${member.name} from this workspace?`)) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/settings/team/${member.membership_id}`, {
        method: "DELETE",
      });
      await resource.reload();
      setToast(`${member.name} was removed.`);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The member could not be removed.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <AppShell surface="user" title="Team">
      <PageHeader
        title="Team access"
        description="Invite trusted people and manage persisted workspace memberships."
        actions={
          <button
            className="button button-primary"
            disabled={resource.source !== "live" || busy}
            title={
              resource.source !== "live"
                ? "Team changes require a live API response."
                : undefined
            }
            onClick={() => setModal(true)}
          >
            ＋ Add teammate
          </button>
        }
      />
      <LiveDataBanner
        label="team memberships"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      <div className="alert alert-info">
        Only owners and admins can change access. The API records every
        membership addition and removal.
      </div>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {resource.status === "loading" ? (
        <StatePanel type="loading" />
      ) : resource.status === "error" ? (
        <StatePanel
          type="error"
          description={resource.error ?? undefined}
          action={
            <button
              className="button button-secondary"
              onClick={() => void resource.reload()}
            >
              Try again
            </button>
          }
        />
      ) : !resource.data?.length ? (
        <StatePanel
          type="empty"
          title="No team members returned"
          description="Add the first member when authenticated as a workspace owner or admin."
        />
      ) : (
        <>
          <Filters search={query} setSearch={setQuery}>
            <select
              className="filter-select"
              aria-label="Filter by status"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="revoked">Revoked</option>
            </select>
          </Filters>
          {rows.length ? (
            <section className="card table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Person</th>
                    <th>Contact</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((member) => (
                    <tr key={member.membership_id}>
                      <td>
                        <div
                          style={{
                            display: "flex",
                            gap: 10,
                            alignItems: "center",
                          }}
                        >
                          <span className="avatar">
                            {member.name
                              .split(" ")
                              .map((part) => part[0])
                              .slice(0, 2)
                              .join("")}
                          </span>
                          <span>
                            <span className="table-primary">{member.name}</span>
                            {member.email && (
                              <span className="table-secondary">
                                {member.email}
                              </span>
                            )}
                          </span>
                        </div>
                      </td>
                      <td>{maskPhone(member.phone_e164)}</td>
                      <td>{titleCase(member.role)}</td>
                      <td>
                        <Badge>{titleCase(member.status)}</Badge>
                      </td>
                      <td>
                        <button
                          className="button button-ghost button-small"
                          disabled={
                            member.role === "owner" ||
                            member.status !== "active" ||
                            resource.source !== "live" ||
                            busy
                          }
                          onClick={() => void remove(member)}
                        >
                          {member.role === "owner"
                            ? "Owner protected"
                            : "Remove"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ) : (
            <StatePanel
              type="empty"
              title="No matching team members"
              description="Clear or adjust the filters."
            />
          )}
        </>
      )}
      {modal && (
        <Modal
          title="Add a teammate"
          onClose={() => !busy && setModal(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => setModal(false)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                disabled={busy || !name || !phone}
                onClick={() => void invite()}
              >
                {busy ? "Adding…" : "Add member"}
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="invite-name">Full name</label>
            <input
              id="invite-name"
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoComplete="name"
            />
          </div>
          <div className="field">
            <label htmlFor="invite-phone">Phone in E.164 format</label>
            <input
              id="invite-phone"
              type="tel"
              className="input"
              placeholder="+234…"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              autoComplete="tel"
            />
          </div>
          <div className="field">
            <label htmlFor="invite-email">Email (optional)</label>
            <input
              id="invite-email"
              type="email"
              className="input"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
            />
          </div>
          <div className="field">
            <label htmlFor="invite-role">Workspace role</label>
            <select
              id="invite-role"
              className="select"
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div
            className="alert alert-warning"
            style={{ marginTop: 18, marginBottom: 0 }}
          >
            This records a membership immediately. WhatsApp invitation delivery
            is not claimed until the Meta integration is active.
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
