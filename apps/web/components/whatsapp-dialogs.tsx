import { Modal } from "@/components/ui";
import { workspaceRoleLabel } from "@/lib/consumer-data";
import { type TeamMember, type WhatsAppNumber } from "@/lib/platform-data";

export function WhatsAppDialogs({
  availableMembers,
  busy,
  label,
  modalOpen,
  onAdd,
  onCloseAdd,
  onCloseRemove,
  onLabelChange,
  onPhoneChange,
  onRemove,
  onUserChange,
  phone,
  removing,
  userId,
}: {
  availableMembers: TeamMember[];
  busy: boolean;
  label: string;
  modalOpen: boolean;
  onAdd: () => Promise<void>;
  onCloseAdd: () => void;
  onCloseRemove: () => void;
  onLabelChange: (value: string) => void;
  onPhoneChange: (value: string) => void;
  onRemove: () => Promise<void>;
  onUserChange: (value: string) => void;
  phone: string;
  removing: WhatsAppNumber | null;
  userId: string;
}) {
  return (
    <>
      {modalOpen && (
        <Modal
          title="Add an approved number"
          onClose={onCloseAdd}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={onCloseAdd}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || !userId || !phone}
                onClick={() => void onAdd()}
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
              onChange={(event) => onUserChange(event.target.value)}
            >
              <option value="">Select a member</option>
              {availableMembers.map((member) => (
                <option value={member.user_id} key={member.user_id}>
                  {member.name} · {workspaceRoleLabel(member.role)}
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
              onChange={(event) => onPhoneChange(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="phone-label">Label (optional)</label>
            <input
              id="phone-label"
              className="input"
              placeholder="Store manager"
              value={label}
              onChange={(event) => onLabelChange(event.target.value)}
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
    </>
  );
}
