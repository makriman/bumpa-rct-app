"use client";

import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import { Badge, Modal, PageHeader, StatePanel, Toast } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import {
  maskPhone,
  titleCase,
  type TeamMember,
  type WhatsAppNumber,
} from "@/lib/platform-data";
import { previewTeam, previewWhatsAppNumbers } from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";

export default function WhatsAppPage() {
  const numbers = useApiResource<WhatsAppNumber[]>(
    "/settings/whatsapp-numbers",
    previewWhatsAppNumbers,
  );
  const team = useApiResource<TeamMember[]>("/settings/team", previewTeam);
  const [modal, setModal] = useState(false);
  const [userId, setUserId] = useState("");
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [removing, setRemoving] = useState<WhatsAppNumber | null>(null);
  const add = async () => {
    setBusy(true);
    setError("");
    try {
      await apiRequest("/settings/whatsapp-numbers", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          phone_e164: phone,
          label: label || null,
        }),
      });
      await numbers.reload();
      setModal(false);
      setUserId("");
      setPhone("");
      setLabel("");
      setToast("Approved phone identity added.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The phone identity could not be added.",
      );
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!removing || busy) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/settings/whatsapp-numbers/${removing.id}`, {
        method: "DELETE",
      });
      await numbers.reload();
      setRemoving(null);
      setToast("WhatsApp access removed for that team identity.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The phone identity could not be removed.",
      );
    } finally {
      setBusy(false);
    }
  };
  const availableMembers = (team.data ?? []).filter(
    (member) => member.status === "active",
  );
  return (
    <AppShell surface="user" title="WhatsApp numbers">
      <PageHeader
        title="Approved WhatsApp numbers"
        description="Review phone identities authorised to access this workspace."
        actions={
          <button
            className="button button-primary"
            disabled={
              numbers.source !== "live" ||
              team.source !== "live" ||
              !availableMembers.length ||
              busy
            }
            onClick={() => setModal(true)}
          >
            ＋ Add number
          </button>
        }
      />
      <LiveDataBanner
        label="approved phone identities"
        source={numbers.source}
        status={numbers.status}
        count={numbers.data?.length}
        error={numbers.error}
      />
      <div className="alert alert-info">
        Unknown numbers are rejected by the webhook. STOP and START update the
        recorded opt-out state after the live Meta webhook is activated.
      </div>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {numbers.status === "loading" ? (
        <StatePanel type="loading" />
      ) : numbers.status === "error" ? (
        <StatePanel
          type="error"
          description={numbers.error ?? undefined}
          action={
            <button
              className="button button-secondary"
              onClick={() => void numbers.reload()}
            >
              Try again
            </button>
          }
        />
      ) : !numbers.data?.length ? (
        <StatePanel
          type="empty"
          title="No approved numbers"
          description="Add an active team member's phone identity when authenticated as an owner or admin."
        />
      ) : (
        <div className="grid">
          {numbers.data.map((number) => (
            <section className="card connection-card" key={number.id}>
              <div className="connection-icon">◌</div>
              <div className="connection-body">
                <strong>{number.label || "Approved team number"}</strong>
                <p>
                  {maskPhone(number.phone_e164)} · user{" "}
                  {number.user_id.slice(0, 8)}
                </p>
              </div>
              <Badge>
                {number.opt_out ? "Opted out" : titleCase(number.status)}
              </Badge>
              {team.data?.find((member) => member.user_id === number.user_id)
                ?.role === "owner" ? (
                <button
                  className="button button-ghost button-small"
                  disabled
                  title="Owner mappings are controlled by a platform administrator."
                >
                  Platform managed
                </button>
              ) : (
                <button
                  className="button button-ghost button-small"
                  disabled={
                    numbers.source !== "live" || team.status !== "ready" || busy
                  }
                  onClick={() => setRemoving(number)}
                >
                  Remove access
                </button>
              )}
            </section>
          ))}
        </div>
      )}
      {modal && (
        <Modal
          title="Add an approved number"
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
                disabled={busy || !userId || !phone}
                onClick={() => void add()}
              >
                {busy ? "Adding…" : "Approve identity"}
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="phone-member">Active team member</label>
            <select
              id="phone-member"
              className="select"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
            >
              <option value="">Select a member</option>
              {availableMembers.map((member) => (
                <option value={member.user_id} key={member.user_id}>
                  {member.name} · {titleCase(member.role)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="new-phone">Phone in E.164 format</label>
            <input
              id="new-phone"
              type="tel"
              className="input"
              placeholder="+234…"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="phone-label">Label (optional)</label>
            <input
              id="phone-label"
              className="input"
              placeholder="Store manager"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>
          <div className="alert alert-warning">
            The current endpoint creates an approved identity; it does not send
            a verification message. Delivery remains a separate integration.
          </div>
        </Modal>
      )}
      {removing && (
        <Modal
          title="Remove WhatsApp access"
          onClose={() => !busy && setRemoving(null)}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => setRemoving(null)}
              >
                Keep access
              </button>
              <button
                className="button button-danger"
                disabled={busy}
                onClick={() => void remove()}
              >
                {busy ? "Removing…" : "Remove access"}
              </button>
            </>
          }
        >
          <p>
            <strong>{removing.label || "This team number"}</strong> will no
            longer route WhatsApp messages into this workspace. The team member
            account itself is unchanged.
          </p>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
