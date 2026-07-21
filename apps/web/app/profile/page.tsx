"use client";

import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/app-shell";
import {
  ProfileDialogs,
  ProfileOverview,
  type ProfileData,
} from "@/components/profile-content";
import { PageHeader, StatePanel, Toast } from "@/components/ui";
import { apiRequest } from "@/lib/api";

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
  role: string | null;
};

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [source, setSource] = useState<"live" | null>(null);
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
    <AppShell title="Profile">
      <PageHeader
        title="Your profile"
        description="Your identity and active workspace, read securely from Bumpa Bestie."
        actions={
          <button
            type="button"
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
              type="button"
              className="button button-primary"
              onClick={() => void loadProfile()}
            >
              Try again
            </button>
          }
        />
      )}

      {status === "ready" && profile && (
        <ProfileOverview
          busy={busy}
          onConfirmSessions={() => setConfirmSessions(true)}
          profile={profile}
          source={source}
        />
      )}
      <ProfileDialogs
        busy={busy}
        confirmSessions={confirmSessions}
        editing={editing}
        email={email}
        error={error}
        name={name}
        onCloseEdit={() => !busy && setEditing(false)}
        onCloseSessions={() => !busy && setConfirmSessions(false)}
        onEmailChange={setEmail}
        onNameChange={setName}
        onSave={saveProfile}
        onSignOutOthers={signOutOthers}
      />
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
