import { Badge, Card, Modal } from "@/components/ui";
import { workspaceRoleLabel } from "@/lib/consumer-data";

export type ProfileData = {
  session: {
    user: {
      id: string;
      name: string;
      email: string | null;
      phone_e164: string;
    };
    platform_roles: string[];
    memberships: Array<{
      id: string;
      tenant_id: string;
      role: string;
      status: string;
    }>;
    current_tenant_id: string | null;
  };
  tenant: {
    id: string;
    slug: string;
    name: string;
    status: string;
    business_category: string | null;
    country: string | null;
    city: string | null;
    timezone: string;
    currency_code: string;
    role: string | null;
  };
};

function initials(name: string): string {
  const value = name
    .trim()
    .split(/\s+/)
    .flatMap((part) => (part[0] ? [part[0]] : []))
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return value || "BB";
}

function displayValue(value: string | null | undefined): string {
  return value?.trim() || "Not provided";
}

function titleCase(value: string | null | undefined): string {
  if (!value) return "Not provided";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function ProfileOverview({
  busy,
  onConfirmSessions,
  profile,
  source,
}: {
  busy: boolean;
  onConfirmSessions: () => void;
  profile: ProfileData;
  source: "live" | null;
}) {
  return (
    <>
      <div className="alert alert-success" role="status">
        <div>
          <strong>Live profile</strong>
          <div>
            These values came from the authenticated user and tenant APIs.
          </div>
        </div>
      </div>
      <div className="grid grid-2">
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Personal details</h2>
              <p>Your verified account identity.</p>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              gap: 16,
              alignItems: "center",
              marginBottom: 10,
            }}
          >
            <span
              className="avatar"
              style={{ width: 64, height: 64, fontSize: 20 }}
              aria-label={`${profile.session.user.name} initials`}
            >
              {initials(profile.session.user.name)}
            </span>
            <div>
              <button
                type="button"
                className="button button-secondary button-small"
                disabled
                title="Profile photo storage is not configured."
              >
                Photo changes unavailable
              </button>
              <div className="field-help" style={{ marginTop: 6 }}>
                No profile photo upload API is available yet.
              </div>
            </div>
          </div>
          <ReadOnlyField
            id="name"
            label="Full name"
            value={profile.session.user.name}
          />
          <ReadOnlyField
            id="email"
            label="Email address"
            type="email"
            value={profile.session.user.email ?? ""}
          />
          <ReadOnlyField
            help="Phone changes require verification and owner approval."
            id="phone"
            label="Approved WhatsApp number"
            value={profile.session.user.phone_e164}
          />
        </Card>
        <div className="grid">
          <WorkspaceCard profile={profile} />
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Session security</h2>
                <p>Your current authenticated session.</p>
              </div>
            </div>
            <div className="alert alert-info">
              Device names and locations are deliberately not exposed. You can
              still revoke every other active token while keeping this session
              open.
            </div>
            <button
              type="button"
              className="button button-secondary"
              disabled={source !== "live" || busy}
              onClick={onConfirmSessions}
            >
              Sign out other sessions
            </button>
          </Card>
        </div>
      </div>
    </>
  );
}

function ReadOnlyField({
  help,
  id,
  label,
  type = "text",
  value,
}: {
  help?: string;
  id: string;
  label: string;
  type?: "email" | "text";
  value: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        value={value}
        placeholder="Not provided"
        disabled
        readOnly
      />
      {help && <span className="field-help">{help}</span>}
    </div>
  );
}

function WorkspaceCard({ profile }: { profile: ProfileData }) {
  const details = [
    ["Business", profile.tenant.name],
    ["Role", workspaceRoleLabel(profile.tenant.role)],
    ["Business category", displayValue(profile.tenant.business_category)],
    [
      "Location",
      [profile.tenant.city, profile.tenant.country]
        .filter(Boolean)
        .join(", ") || "Not provided",
    ],
    ["Timezone", profile.tenant.timezone],
    ["Currency", profile.tenant.currency_code],
  ];
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>Workspace</h2>
          <p>Your active tenant context.</p>
        </div>
        <Badge
          tone={
            profile.tenant.status.toLowerCase() === "active"
              ? "success"
              : "warning"
          }
        >
          {titleCase(profile.tenant.status)}
        </Badge>
      </div>
      <div className="detail-list">
        {details.map(([label, value]) => (
          <div className="detail-row" key={label}>
            <span className="detail-label">{label}</span>
            <span className="detail-value">{value}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function ProfileDialogs({
  busy,
  confirmSessions,
  editing,
  email,
  error,
  name,
  onCloseEdit,
  onCloseSessions,
  onEmailChange,
  onNameChange,
  onSave,
  onSignOutOthers,
}: {
  busy: boolean;
  confirmSessions: boolean;
  editing: boolean;
  email: string;
  error: string | null;
  name: string;
  onCloseEdit: () => void;
  onCloseSessions: () => void;
  onEmailChange: (value: string) => void;
  onNameChange: (value: string) => void;
  onSave: () => Promise<void>;
  onSignOutOthers: () => Promise<void>;
}) {
  return (
    <>
      {editing && (
        <Modal
          title="Edit profile"
          onClose={onCloseEdit}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={onCloseEdit}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || !name.trim()}
                onClick={() => void onSave()}
              >
                {busy ? "Saving…" : "Save changes"}
              </button>
            </>
          }
        >
          <label className="field" htmlFor="edit-name">
            <span>Full name</span>
            <input
              id="edit-name"
              className="input"
              value={name}
              maxLength={200}
              onChange={(event) => onNameChange(event.target.value)}
              disabled={busy}
            />
          </label>
          <label className="field" htmlFor="edit-email">
            <span>Email address</span>
            <input
              id="edit-email"
              type="email"
              className="input"
              value={email}
              onChange={(event) => onEmailChange(event.target.value)}
              disabled={busy}
            />
          </label>
          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}
        </Modal>
      )}
      {confirmSessions && (
        <Modal
          title="Sign out other sessions"
          onClose={onCloseSessions}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={onCloseSessions}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button button-danger"
                disabled={busy}
                onClick={() => void onSignOutOthers()}
              >
                {busy ? "Signing out…" : "Sign out other sessions"}
              </button>
            </>
          }
        >
          <p>
            Every other active login token for this account will be revoked.
            This browser stays signed in.
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
