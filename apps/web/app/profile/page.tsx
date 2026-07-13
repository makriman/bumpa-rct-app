"use client";

import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/app-shell";
import {
  Badge,
  Card,
  Modal,
  PageHeader,
  StatePanel,
  Toast,
} from "@/components/ui";
import { apiRequest, isDemoMode } from "@/lib/api";
import { currentUser } from "@/lib/demo-data";

type SessionView = {
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

type TenantView = {
  id: string;
  slug: string;
  name: string;
  status: string;
  business_category: string | null;
  country: string | null;
  city: string | null;
  timezone: string;
  currency_code: string;
  research_consent_status: string;
  role: string | null;
};

type ProfileData = {
  session: SessionView;
  tenant: TenantView;
};

const demoProfile: ProfileData = {
  session: {
    user: {
      id: "demo-user",
      name: currentUser.name,
      email: currentUser.email,
      phone_e164: currentUser.phone,
    },
    platform_roles: [],
    memberships: [
      {
        id: "demo-membership",
        tenant_id: "demo-tenant",
        role: currentUser.role,
        status: "active",
      },
    ],
    current_tenant_id: "demo-tenant",
  },
  tenant: {
    id: "demo-tenant",
    slug: "kaia-home-demo",
    name: currentUser.tenant,
    status: "active",
    business_category: "Home & living",
    country: "Nigeria",
    city: "Lagos",
    timezone: currentUser.timezone,
    currency_code: currentUser.currency,
    research_consent_status: "granted",
    role: currentUser.role,
  },
};

function initials(name: string): string {
  const value = name
    .trim()
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
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

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [source, setSource] = useState<"live" | "demo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmSessions, setConfirmSessions] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");

  const loadProfile = useCallback(async () => {
    setStatus("loading");
    setError(null);
    if (isDemoMode) {
      setProfile(demoProfile);
      setSource("demo");
      setStatus("ready");
      return;
    }
    try {
      const [session, tenant] = await Promise.all([
        apiRequest<SessionView>("/auth/me"),
        apiRequest<TenantView>("/tenants/current"),
      ]);
      setProfile({ session, tenant });
      setSource("live");
      setStatus("ready");
    } catch (reason) {
      setProfile(null);
      setSource(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "Profile information is unavailable.",
      );
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  const openEdit = () => {
    if (!profile) return;
    setName(profile.session.user.name);
    setEmail(profile.session.user.email ?? "");
    setError(null);
    setEditing(true);
  };

  const saveProfile = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiRequest("/settings/profile", {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          email: email.trim() || null,
        }),
      });
      await loadProfile();
      setEditing(false);
      setToast("Profile details updated.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Your profile could not be updated.",
      );
    } finally {
      setBusy(false);
    }
  };

  const signOutOthers = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await apiRequest<{ revoked_sessions: number }>(
        "/auth/logout-others",
        { method: "POST" },
      );
      setConfirmSessions(false);
      setToast(
        result.revoked_sessions
          ? `${result.revoked_sessions} other session${result.revoked_sessions === 1 ? "" : "s"} signed out.`
          : "No other active sessions were found.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Other sessions could not be signed out.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell surface="user" title="Profile">
      <PageHeader
        title="Your profile"
        description="Your identity and active workspace, read securely from Bumpa Bestie."
        actions={
          <button
            className="button button-secondary"
            disabled={source !== "live" || status !== "ready" || busy}
            onClick={openEdit}
          >
            Edit profile
          </button>
        }
      />

      {status === "loading" && <StatePanel type="loading" />}

      {status === "error" && (
        <StatePanel
          type="error"
          title="We could not load your profile"
          description={error ?? "Profile information is unavailable."}
          action={
            <button
              className="button button-primary"
              onClick={() => void loadProfile()}
            >
              Try again
            </button>
          }
        />
      )}

      {status === "ready" && profile && (
        <>
          <div
            className={`alert ${source === "demo" ? "alert-warning" : "alert-success"}`}
            role="status"
          >
            <div>
              <strong>
                {source === "demo" ? "Demo profile preview" : "Live profile"}
              </strong>
              <div>
                {source === "demo"
                  ? "These values are illustrative and are not tenant or user data."
                  : "These values came from the authenticated user and tenant APIs."}
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
              <div className="field">
                <label htmlFor="name">Full name</label>
                <input
                  id="name"
                  className="input"
                  value={profile.session.user.name}
                  disabled
                  readOnly
                />
              </div>
              <div className="field">
                <label htmlFor="email">Email address</label>
                <input
                  id="email"
                  className="input"
                  type="email"
                  value={profile.session.user.email ?? ""}
                  placeholder="Not provided"
                  disabled
                  readOnly
                />
              </div>
              <div className="field">
                <label htmlFor="phone">Approved WhatsApp number</label>
                <input
                  id="phone"
                  className="input"
                  value={profile.session.user.phone_e164}
                  disabled
                  readOnly
                />
                <span className="field-help">
                  Phone changes require verification and owner approval.
                </span>
              </div>
            </Card>
            <div className="grid">
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
                  <div className="detail-row">
                    <span className="detail-label">Business</span>
                    <span className="detail-value">{profile.tenant.name}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Role</span>
                    <span className="detail-value">
                      {titleCase(profile.tenant.role)}
                    </span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Business category</span>
                    <span className="detail-value">
                      {displayValue(profile.tenant.business_category)}
                    </span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Location</span>
                    <span className="detail-value">
                      {[profile.tenant.city, profile.tenant.country]
                        .filter(Boolean)
                        .join(", ") || "Not provided"}
                    </span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Timezone</span>
                    <span className="detail-value">
                      {profile.tenant.timezone}
                    </span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Currency</span>
                    <span className="detail-value">
                      {profile.tenant.currency_code}
                    </span>
                  </div>
                </div>
              </Card>
              <Card padded>
                <div className="card-head">
                  <div>
                    <h2>Session security</h2>
                    <p>Your current authenticated session.</p>
                  </div>
                </div>
                <div className="alert alert-info">
                  Device names and locations are deliberately not exposed. You
                  can still revoke every other active token while keeping this
                  session open.
                </div>
                <button
                  className="button button-secondary"
                  disabled={source !== "live" || busy}
                  onClick={() => setConfirmSessions(true)}
                >
                  Sign out other sessions
                </button>
              </Card>
            </div>
          </div>
        </>
      )}
      {editing && (
        <Modal
          title="Edit profile"
          onClose={() => !busy && setEditing(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => setEditing(false)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                disabled={busy || !name.trim()}
                onClick={() => void saveProfile()}
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
              onChange={(event) => setName(event.target.value)}
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
              onChange={(event) => setEmail(event.target.value)}
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
          onClose={() => !busy && setConfirmSessions(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => setConfirmSessions(false)}
              >
                Cancel
              </button>
              <button
                className="button button-danger"
                disabled={busy}
                onClick={() => void signOutOthers()}
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
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
