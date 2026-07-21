import { Modal } from "@/components/ui";
import type { TeamMember } from "@/lib/platform-data";

export type TeamFormState = {
  email: string;
  name: string;
  phone: string;
  role: string;
};

export function TeamDialogs({
  busy,
  error,
  form,
  inviteOpen,
  onCloseInvite,
  onCloseRemove,
  onFormChange,
  onInvite,
  onRemove,
  removing,
}: {
  busy: boolean;
  error: string;
  form: TeamFormState;
  inviteOpen: boolean;
  onCloseInvite: () => void;
  onCloseRemove: () => void;
  onFormChange: (value: Partial<TeamFormState>) => void;
  onInvite: () => Promise<void>;
  onRemove: () => Promise<void>;
  removing: TeamMember | null;
}) {
  return (
    <>
      {inviteOpen && (
        <Modal
          title="Add a teammate"
          onClose={onCloseInvite}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={onCloseInvite}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || !form.name || !form.phone}
                onClick={() => void onInvite()}
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
              value={form.name}
              onChange={(event) => onFormChange({ name: event.target.value })}
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
              value={form.phone}
              onChange={(event) => onFormChange({ phone: event.target.value })}
              autoComplete="tel"
            />
          </div>
          <div className="field">
            <label htmlFor="invite-email">Email (optional)</label>
            <input
              id="invite-email"
              type="email"
              className="input"
              value={form.email}
              onChange={(event) => onFormChange({ email: event.target.value })}
              autoComplete="email"
            />
          </div>
          <div className="field">
            <label htmlFor="invite-role">Workspace role</label>
            <select
              id="invite-role"
              className="select"
              value={form.role}
              onChange={(event) => onFormChange({ role: event.target.value })}
            >
              <option value="member">Member</option>
              <option value="admin">Manager</option>
            </select>
          </div>
          <div
            className="alert alert-warning"
            style={{ marginTop: 18, marginBottom: 0 }}
          >
            This records a membership immediately. WhatsApp invitation delivery
            is not claimed until the Meta integration is active.
          </div>
          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}
        </Modal>
      )}
      {removing && (
        <Modal
          title={`Remove ${removing.name}?`}
          onClose={onCloseRemove}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={onCloseRemove}
              >
                Keep access
              </button>
              <button
                type="button"
                className="button button-danger"
                disabled={busy}
                onClick={() => void onRemove()}
              >
                {busy ? "Removing…" : "Remove teammate"}
              </button>
            </>
          }
        >
          <p>
            Their active membership in this workspace will be revoked. This does
            not delete their identity or any audit history.
          </p>
          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
