"use client";

import { useState } from "react";
import { AppIcon } from "@/components/app-icon";
import { apiRequest } from "@/lib/api";
import {
  formatDate,
  titleCase,
  type AuditEvent,
  type Tenant,
  type TenantOperations,
} from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import {
  Badge,
  Card,
  Modal,
  PageHeader,
  ScrollableTable,
  StatePanel,
  Toast,
} from "./ui";

type TenantTab =
  | "Overview"
  | "People & phones"
  | "Bumpa"
  | "Hermes"
  | "Audit log";
type TenantModal = "tenant" | "user" | "phone" | "bumpa" | null;
type ConfirmedAction = "sync" | "restart" | "suspend" | null;

const tabs: TenantTab[] = [
  "Overview",
  "People & phones",
  "Bumpa",
  "Hermes",
  "Audit log",
];

function tenantLabel(tenant: Tenant): string {
  return [titleCase(tenant.business_category), tenant.city]
    .filter((value) => value && value !== "Not available")
    .join(" · ");
}

function useTenantDetail(id: string) {
  const resource = useApiResource<Tenant>(`/admin/tenants/${id}`);
  const operations = useApiResource<TenantOperations>(
    `/admin/tenants/${id}/operations`,
  );
  const audit = useApiResource<AuditEvent[]>("/admin/audit");
  const [tab, setTab] = useState<TenantTab>("Overview");
  const [modal, setModal] = useState<TenantModal>(null);
  const [confirmation, setConfirmation] = useState<ConfirmedAction>(null);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [tenantForm, setTenantForm] = useState({
    name: "",
    status: "active",
    business_category: "",
    city: "",
    timezone: "Africa/Lagos",
  });
  const [userForm, setUserForm] = useState({
    name: "",
    phone_e164: "",
    email: "",
    role: "member",
  });
  const [phoneForm, setPhoneForm] = useState({
    user_id: "",
    phone_e164: "",
    label: "Team member",
  });
  const [bumpaForm, setBumpaForm] = useState({
    api_key: "",
    scope_type: "business_id",
    scope_id: "",
    store_timezone: "Africa/Lagos",
    store_currency: "NGN",
  });

  const fail = (reason: unknown, fallback: string) => {
    setError(reason instanceof Error ? reason.message : fallback);
  };

  const saveTenant = async () => {
    const tenant = resource.data;
    if (!tenant || !tenantForm.name.trim()) return;
    setSaving(true);
    setError("");
    try {
      const updated = await apiRequest<Tenant>(`/admin/tenants/${tenant.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...tenantForm,
          business_category: tenantForm.business_category.trim() || null,
          city: tenantForm.city.trim() || null,
        }),
      });
      resource.replace(updated);
      setModal(null);
      setToast("Tenant details updated and audit logged.");
    } catch (reason) {
      fail(reason, "The tenant details could not be updated.");
    } finally {
      setSaving(false);
    }
  };

  const addUser = async () => {
    const tenant = resource.data;
    if (!tenant || !userForm.name.trim() || !userForm.phone_e164.trim()) return;
    setSaving(true);
    setError("");
    try {
      await apiRequest(`/admin/tenants/${tenant.id}/users`, {
        method: "POST",
        body: JSON.stringify({
          ...userForm,
          email: userForm.email.trim() || null,
        }),
      });
      await operations.reload();
      setModal(null);
      setUserForm({ name: "", phone_e164: "", email: "", role: "member" });
      setToast("Tenant member added and recorded in the audit trail.");
    } catch (reason) {
      fail(reason, "The member could not be added.");
    } finally {
      setSaving(false);
    }
  };

  const approvePhone = async () => {
    const tenant = resource.data;
    if (!tenant || !phoneForm.user_id || !phoneForm.phone_e164.trim()) return;
    setSaving(true);
    setError("");
    try {
      await apiRequest(`/admin/tenants/${tenant.id}/phones`, {
        method: "POST",
        body: JSON.stringify(phoneForm),
      });
      await operations.reload();
      setModal(null);
      setPhoneForm({ user_id: "", phone_e164: "", label: "Team member" });
      setToast("WhatsApp number approved for this tenant.");
    } catch (reason) {
      fail(reason, "The number could not be approved.");
    } finally {
      setSaving(false);
    }
  };

  const saveBumpa = async () => {
    const tenant = resource.data;
    if (!tenant || !bumpaForm.api_key || !bumpaForm.scope_id.trim()) return;
    setSaving(true);
    setError("");
    try {
      await apiRequest(`/admin/tenants/${tenant.id}/bumpa`, {
        method: "POST",
        body: JSON.stringify({ ...bumpaForm, provider: "bumpa" }),
      });
      await operations.reload();
      setModal(null);
      setBumpaForm({
        api_key: "",
        scope_type: "business_id",
        scope_id: "",
        store_timezone: tenant.timezone,
        store_currency: tenant.currency_code,
      });
      setToast(
        "Bumpa connection verified. The API key is no longer displayed.",
      );
    } catch (reason) {
      setBumpaForm((current) => ({ ...current, api_key: "" }));
      fail(reason, "The Bumpa connection could not be verified.");
    } finally {
      setSaving(false);
    }
  };

  const triggerSync = async () => {
    const tenant = resource.data;
    if (!tenant || resource.source !== "live" || syncing) return;
    const dateTo = new Date();
    const dateFrom = new Date(dateTo);
    dateFrom.setUTCDate(dateFrom.getUTCDate() - 29);
    setSyncing(true);
    setError("");
    try {
      await apiRequest(`/admin/tenants/${tenant.id}/bumpa/sync`, {
        method: "POST",
        headers: { "Idempotency-Key": `admin-${tenant.id}-${Date.now()}` },
        body: JSON.stringify({
          date_from: dateFrom.toISOString().slice(0, 10),
          date_to: dateTo.toISOString().slice(0, 10),
          reason: "operator_requested_refresh",
          confirmation: "trigger_bumpa_sync",
        }),
      });
      setToast("Bumpa refresh queued with an audited operation ID.");
    } catch (reason) {
      fail(reason, "The Bumpa refresh could not be queued.");
    } finally {
      setSyncing(false);
    }
  };

  const restartHermes = async () => {
    const tenant = resource.data;
    if (!tenant || operations.source !== "live" || restarting) return;
    setRestarting(true);
    setError("");
    try {
      await apiRequest(`/admin/tenants/${tenant.id}/hermes-profile/restart`, {
        method: "POST",
        body: JSON.stringify({
          reason: "operator_health_recovery",
          confirmation: "restart_hermes_profile",
        }),
      });
      await operations.reload();
      setToast("Hermes restart accepted by the private control plane.");
    } catch (reason) {
      fail(reason, "The Hermes profile could not be restarted.");
    } finally {
      setRestarting(false);
    }
  };

  const suspendTenant = async () => {
    const tenant = resource.data;
    if (!tenant || resource.source !== "live") return;
    setSaving(true);
    setError("");
    try {
      const updated = await apiRequest<Tenant>(`/admin/tenants/${tenant.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "suspended" }),
      });
      resource.replace(updated);
      setToast(`${tenant.name} is suspended.`);
    } catch (reason) {
      fail(reason, "The tenant could not be suspended.");
    } finally {
      setSaving(false);
    }
  };

  const confirmAction = async () => {
    const action = confirmation;
    setConfirmation(null);
    if (action === "sync") await triggerSync();
    if (action === "restart") await restartHermes();
    if (action === "suspend") await suspendTenant();
  };

  const openTenantEditor = () => {
    const tenant = resource.data;
    if (!tenant) return;
    setTenantForm({
      name: tenant.name,
      status: tenant.status,
      business_category: tenant.business_category ?? "",
      city: tenant.city ?? "",
      timezone: tenant.timezone,
    });
    setModal("tenant");
  };

  return {
    addUser,
    approvePhone,
    audit,
    bumpaForm,
    confirmAction,
    confirmation,
    error,
    modal,
    openTenantEditor,
    operations,
    phoneForm,
    resource,
    restarting,
    saveBumpa,
    saveTenant,
    saving,
    setBumpaForm,
    setConfirmation,
    setModal,
    setPhoneForm,
    setTab,
    setTenantForm,
    setToast,
    setUserForm,
    syncing,
    tab,
    tenantForm,
    toast,
    userForm,
  };
}

type TenantDetailView = ReturnType<typeof useTenantDetail>;

export function TenantDetail({ id }: { id: string }) {
  const view = useTenantDetail(id);
  const tenant = view.resource.data;
  return (
    <AppShell title={tenant?.name ?? "Tenant detail"}>
      <PageHeader
        title={tenant?.name ?? "Tenant detail"}
        description={
          tenant
            ? tenantLabel(tenant) || "Business workspace"
            : "Loading business workspace."
        }
        actions={<TenantHeaderActions view={view} />}
      />
      <LiveDataBanner
        label="tenant record"
        source={view.resource.source}
        status={view.resource.status}
        error={view.resource.error}
      />
      {view.error && (
        <div className="alert alert-danger" role="alert">
          {view.error}
        </div>
      )}
      {view.resource.status !== "ready" || !tenant ? (
        <ResourcePanel
          status={view.resource.status}
          error={view.resource.error}
          onRetry={view.resource.reload}
        />
      ) : (
        <>
          <TenantTabs tab={view.tab} onChange={view.setTab} />
          <TenantTabContent tenant={tenant} view={view} />
        </>
      )}
      <TenantDialogs view={view} />
      {view.toast && (
        <Toast message={view.toast} onClose={() => view.setToast("")} />
      )}
    </AppShell>
  );
}

function TenantHeaderActions({ view }: { view: TenantDetailView }) {
  const tenant = view.resource.data;
  return (
    <>
      <button
        type="button"
        className="button button-secondary"
        disabled={!tenant || view.resource.source !== "live" || view.saving}
        onClick={view.openTenantEditor}
      >
        Edit details
      </button>
      <button
        type="button"
        className="button button-secondary"
        disabled={
          !tenant ||
          view.resource.source !== "live" ||
          !view.operations.data?.bumpa.connected ||
          view.syncing
        }
        onClick={() => view.setConfirmation("sync")}
      >
        {view.syncing ? (
          "Queueing…"
        ) : (
          <>
            <AppIcon name="refresh" size={16} /> Trigger sync
          </>
        )}
      </button>
      <button
        type="button"
        className="button button-danger"
        disabled={
          !tenant ||
          view.resource.source !== "live" ||
          tenant.status === "suspended" ||
          view.saving
        }
        onClick={() => view.setConfirmation("suspend")}
      >
        {view.saving ? "Suspending…" : "Suspend tenant"}
      </button>
    </>
  );
}

function ResourcePanel({
  status,
  error,
  onRetry,
}: {
  status: "loading" | "ready" | "error";
  error: string | null;
  onRetry: () => Promise<void>;
}) {
  if (status === "loading") return <StatePanel type="loading" />;
  return (
    <StatePanel
      type="error"
      description={error ?? undefined}
      action={
        <button
          type="button"
          className="button button-secondary"
          onClick={() => void onRetry()}
        >
          Try again
        </button>
      }
    />
  );
}

function TenantTabs({
  tab,
  onChange,
}: {
  tab: TenantTab;
  onChange: (tab: TenantTab) => void;
}) {
  return (
    <div className="tabs" role="tablist" aria-label="Tenant detail sections">
      {tabs.map((name) => (
        <button
          type="button"
          role="tab"
          aria-selected={tab === name}
          className={`tab ${tab === name ? "active" : ""}`}
          key={name}
          onClick={() => onChange(name)}
        >
          {name}
        </button>
      ))}
    </div>
  );
}

function TenantTabContent({
  tenant,
  view,
}: {
  tenant: Tenant;
  view: TenantDetailView;
}) {
  if (view.tab === "Overview")
    return <OverviewTab tenant={tenant} operations={view.operations.data} />;
  if (view.tab === "People & phones") return <PeopleTab view={view} />;
  if (view.tab === "Bumpa") return <BumpaTab tenant={tenant} view={view} />;
  if (view.tab === "Hermes") return <HermesTab view={view} />;
  return <AuditTab tenant={tenant} view={view} />;
}

function OverviewTab({
  tenant,
  operations,
}: {
  tenant: Tenant;
  operations: TenantOperations | null;
}) {
  return (
    <div className="grid grid-2">
      <Card padded>
        <div className="card-head">
          <div>
            <h2>Tenant details</h2>
            <p>Identity and data-governance configuration.</p>
          </div>
          <Badge>{titleCase(tenant.status)}</Badge>
        </div>
        {[
          ["Tenant ID", tenant.id],
          ["Slug", tenant.slug],
          ["Timezone", tenant.timezone],
          ["Currency", tenant.currency_code],
          ["Research consent", titleCase(tenant.research_consent_status)],
          ["Created", formatDate(tenant.created_at)],
        ].map(([label, value]) => (
          <DetailRow key={label} label={label} value={value} />
        ))}
      </Card>
      <Card padded>
        <div className="card-head">
          <div>
            <h2>Readiness evidence</h2>
            <p>Only evidence exposed by the current API is shown.</p>
          </div>
        </div>
        <StatusRow label="Tenant record" value="Complete" />
        <StatusRow
          label="Active access"
          value={tenant.status === "active" ? "Complete" : "Unavailable"}
        />
        <StatusRow
          label="Research consent"
          value={titleCase(tenant.research_consent_status)}
        />
        <StatusRow
          label="People"
          value={String(operations?.people.length ?? "—")}
        />
        <StatusRow label="Bumpa" value={titleCase(operations?.bumpa.status)} />
        <StatusRow
          label="Hermes"
          value={titleCase(operations?.hermes.status)}
        />
      </Card>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <span className="detail-value">{value}</span>
    </div>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <Badge>{value}</Badge>
    </div>
  );
}

function PeopleTab({ view }: { view: TenantDetailView }) {
  const operations = view.operations;
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>People and approved WhatsApp numbers</h2>
          <p>Numbers are masked after approval; roles remain tenant-scoped.</p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="button button-secondary button-small"
            disabled={operations.source !== "live"}
            onClick={() => view.setModal("phone")}
          >
            Approve number
          </button>
          <button
            type="button"
            className="button button-primary button-small"
            disabled={operations.source !== "live"}
            onClick={() => view.setModal("user")}
          >
            <AppIcon name="user" size={16} /> Add person
          </button>
        </div>
      </div>
      {operations.status !== "ready" ? (
        <ResourcePanel
          status={operations.status}
          error={operations.error}
          onRetry={operations.reload}
        />
      ) : operations.data?.people.length ? (
        <ScrollableTable label="Tenant people and approved numbers">
          <table className="data-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Membership</th>
                <th>Approved numbers</th>
              </tr>
            </thead>
            <tbody>
              {operations.data.people.map((person) => {
                const phones = operations.data?.phones.filter(
                  (phone) => phone.user_id === person.user_id,
                );
                return (
                  <tr key={person.membership_id}>
                    <td>
                      <span className="table-primary">
                        {person.name || "Unnamed member"}
                      </span>
                      <div className="table-secondary">
                        {person.phone_masked}
                      </div>
                    </td>
                    <td>
                      <Badge>{titleCase(person.role)}</Badge>
                    </td>
                    <td>
                      <Badge>{titleCase(person.status)}</Badge>
                    </td>
                    <td>
                      {phones?.length
                        ? phones.map((phone) => phone.phone_masked).join(", ")
                        : "None"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollableTable>
      ) : (
        <p className="table-secondary">No tenant members were returned.</p>
      )}
    </Card>
  );
}

function BumpaTab({
  tenant,
  view,
}: {
  tenant: Tenant;
  view: TenantDetailView;
}) {
  const bumpa = view.operations.data?.bumpa;
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>Bumpa connection</h2>
          <p>Credential values are write-only and never returned here.</p>
        </div>
        <Badge>{titleCase(bumpa?.status)}</Badge>
      </div>
      {[
        ["Provider", titleCase(bumpa?.provider)],
        ["Scope", titleCase(bumpa?.scope_type)],
        [
          "Scope reference",
          bumpa?.scope_id_last4 ? `••••${bumpa.scope_id_last4}` : "Not set",
        ],
        ["Store timezone", bumpa?.store_timezone ?? "Not set"],
        ["Store currency", bumpa?.store_currency ?? "Not set"],
        ["Last successful sync", formatDate(bumpa?.last_successful_sync_at)],
        ["Last failed sync", formatDate(bumpa?.last_failed_sync_at)],
      ].map(([label, value]) => (
        <DetailRow key={label} label={label} value={value} />
      ))}
      <button
        type="button"
        className="button button-secondary"
        disabled={view.operations.source !== "live"}
        onClick={() => {
          view.setBumpaForm((current) => ({
            ...current,
            store_timezone: bumpa?.store_timezone ?? tenant.timezone,
            store_currency: bumpa?.store_currency ?? tenant.currency_code,
          }));
          view.setModal("bumpa");
        }}
        style={{ marginTop: 16 }}
      >
        {bumpa?.connected ? "Replace API key" : "Connect Bumpa"}
      </button>
    </Card>
  );
}

function HermesTab({ view }: { view: TenantDetailView }) {
  const hermes = view.operations.data?.hermes;
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>Hermes profile</h2>
          <p>Lifecycle control is isolated to this authenticated profile.</p>
        </div>
        <Badge>{titleCase(hermes?.status)}</Badge>
      </div>
      <DetailRow
        label="Profile"
        value={hermes?.profile_name ?? "Not provisioned"}
      />
      <DetailRow label="Provider" value={titleCase(hermes?.provider)} />
      <DetailRow
        label="Runtime port"
        value={hermes?.api_port ? String(hermes.api_port) : "Not allocated"}
      />
      <button
        type="button"
        className="button button-danger"
        disabled={
          view.operations.source !== "live" ||
          !hermes?.provisioned ||
          hermes.provider !== "hermes" ||
          view.restarting
        }
        onClick={() => view.setConfirmation("restart")}
        style={{ marginTop: 16 }}
      >
        {view.restarting ? "Restarting…" : "Restart profile"}
      </button>
    </Card>
  );
}

function AuditTab({
  tenant,
  view,
}: {
  tenant: Tenant;
  view: TenantDetailView;
}) {
  const events = (view.audit.data ?? []).filter(
    (event) => event.tenant_id === tenant.id,
  );
  if (view.audit.status !== "ready") {
    return (
      <ResourcePanel
        status={view.audit.status}
        error={view.audit.error}
        onRetry={view.audit.reload}
      />
    );
  }
  return (
    <Card padded>
      <div className="timeline">
        {events.map((event) => (
          <div className="timeline-item" key={event.id}>
            <strong>{event.action}</strong>
            <p>
              {titleCase(event.resource_type)} · {formatDate(event.created_at)}
            </p>
          </div>
        ))}
        {!events.length && (
          <p className="table-secondary">
            No audit events were returned for this tenant.
          </p>
        )}
      </div>
    </Card>
  );
}

function TenantDialogs({ view }: { view: TenantDetailView }) {
  return (
    <>
      {view.modal === "tenant" && <TenantEditor view={view} />}
      {view.modal === "user" && <MemberEditor view={view} />}
      {view.modal === "phone" && <PhoneEditor view={view} />}
      {view.modal === "bumpa" && <BumpaEditor view={view} />}
      {view.confirmation && (
        <ConfirmationDialog view={view} action={view.confirmation} />
      )}
    </>
  );
}

function TenantEditor({ view }: { view: TenantDetailView }) {
  const form = view.tenantForm;
  const update = (value: Partial<typeof form>) =>
    view.setTenantForm((current) => ({ ...current, ...value }));
  return (
    <Modal
      title="Edit tenant details"
      onClose={() => !view.saving && view.setModal(null)}
      actions={
        <EditorActions
          busy={view.saving}
          disabled={!form.name.trim()}
          onCancel={() => view.setModal(null)}
          onSave={view.saveTenant}
          saveLabel="Save changes"
        />
      }
    >
      <TextField
        id="tenant-edit-name"
        label="Business name"
        value={form.name}
        onChange={(value) => update({ name: value })}
      />
      <div className="field">
        <label htmlFor="tenant-edit-status">Status</label>
        <select
          id="tenant-edit-status"
          className="select"
          value={form.status}
          onChange={(event) => update({ status: event.target.value })}
        >
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="archived">Archived</option>
        </select>
      </div>
      <TextField
        id="tenant-edit-category"
        label="Business category"
        value={form.business_category}
        onChange={(value) => update({ business_category: value })}
      />
      <TextField
        id="tenant-edit-city"
        label="City"
        value={form.city}
        onChange={(value) => update({ city: value })}
      />
      <TextField
        id="tenant-edit-timezone"
        label="Timezone"
        value={form.timezone}
        onChange={(value) => update({ timezone: value })}
      />
    </Modal>
  );
}

function MemberEditor({ view }: { view: TenantDetailView }) {
  const form = view.userForm;
  const update = (value: Partial<typeof form>) =>
    view.setUserForm((current) => ({ ...current, ...value }));
  return (
    <Modal
      title="Add tenant member"
      onClose={() => !view.saving && view.setModal(null)}
      actions={
        <EditorActions
          busy={view.saving}
          disabled={!form.name.trim() || !form.phone_e164.trim()}
          onCancel={() => view.setModal(null)}
          onSave={view.addUser}
          saveLabel="Add member"
        />
      }
    >
      <TextField
        id="tenant-user-name"
        label="Name"
        value={form.name}
        autoComplete="name"
        onChange={(value) => update({ name: value })}
      />
      <TextField
        id="tenant-user-phone"
        label="Phone in E.164 format"
        value={form.phone_e164}
        type="tel"
        autoComplete="tel"
        placeholder="+2348012345678"
        onChange={(value) => update({ phone_e164: value })}
      />
      <TextField
        id="tenant-user-email"
        label="Email (optional)"
        value={form.email}
        type="email"
        autoComplete="email"
        onChange={(value) => update({ email: value })}
      />
      <div className="field">
        <label htmlFor="tenant-user-role">Tenant role</label>
        <select
          id="tenant-user-role"
          className="select"
          value={form.role}
          onChange={(event) => update({ role: event.target.value })}
        >
          <option value="member">Member</option>
          <option value="admin">Admin</option>
          <option value="owner">Owner</option>
        </select>
      </div>
    </Modal>
  );
}

function PhoneEditor({ view }: { view: TenantDetailView }) {
  const form = view.phoneForm;
  const update = (value: Partial<typeof form>) =>
    view.setPhoneForm((current) => ({ ...current, ...value }));
  return (
    <Modal
      title="Approve WhatsApp number"
      onClose={() => !view.saving && view.setModal(null)}
      actions={
        <EditorActions
          busy={view.saving}
          disabled={!form.user_id || !form.phone_e164.trim()}
          onCancel={() => view.setModal(null)}
          onSave={view.approvePhone}
          saveLabel="Approve number"
        />
      }
    >
      <div className="alert alert-info">
        Approval maps this login and WhatsApp identity to this tenant. The
        number is masked after saving.
      </div>
      <div className="field">
        <label htmlFor="tenant-phone-user">Tenant member</label>
        <select
          id="tenant-phone-user"
          className="select"
          value={form.user_id}
          onChange={(event) => update({ user_id: event.target.value })}
        >
          <option value="">Select a person</option>
          {view.operations.data?.people.map((person) => (
            <option key={person.user_id} value={person.user_id}>
              {person.name || person.phone_masked}
            </option>
          ))}
        </select>
      </div>
      <TextField
        id="tenant-phone-value"
        label="Phone in E.164 format"
        value={form.phone_e164}
        type="tel"
        placeholder="+2348012345678"
        onChange={(value) => update({ phone_e164: value })}
      />
      <TextField
        id="tenant-phone-label"
        label="Label"
        value={form.label}
        onChange={(value) => update({ label: value })}
      />
    </Modal>
  );
}

function BumpaEditor({ view }: { view: TenantDetailView }) {
  const form = view.bumpaForm;
  const update = (value: Partial<typeof form>) =>
    view.setBumpaForm((current) => ({ ...current, ...value }));
  const connected = view.operations.data?.bumpa.connected;
  return (
    <Modal
      title={connected ? "Replace Bumpa credential" : "Connect Bumpa"}
      onClose={() => !view.saving && view.setModal(null)}
      actions={
        <EditorActions
          busy={view.saving}
          disabled={
            !form.api_key ||
            !form.scope_id.trim() ||
            !form.store_timezone.trim() ||
            form.store_currency.length !== 3
          }
          onCancel={() => view.setModal(null)}
          onSave={view.saveBumpa}
          saveLabel="Verify and save"
        />
      }
    >
      <div className="alert alert-warning">
        The replacement is verified before activation. It is encrypted at rest
        and never displayed again.
      </div>
      <TextField
        id="tenant-bumpa-key"
        label="Bumpa API key"
        value={form.api_key}
        type="password"
        autoComplete="off"
        onChange={(value) => update({ api_key: value })}
      />
      <div className="field">
        <label htmlFor="tenant-bumpa-scope-type">Scope type</label>
        <select
          id="tenant-bumpa-scope-type"
          className="select"
          value={form.scope_type}
          onChange={(event) => update({ scope_type: event.target.value })}
        >
          <option value="business_id">Business ID</option>
          <option value="location_id">Location ID</option>
        </select>
      </div>
      <TextField
        id="tenant-bumpa-scope-id"
        label="Scope ID"
        value={form.scope_id}
        onChange={(value) => update({ scope_id: value })}
      />
      <TextField
        id="tenant-bumpa-timezone"
        label="Store timezone"
        value={form.store_timezone}
        help="IANA timezone used for Bumpa reporting-day boundaries."
        onChange={(value) => update({ store_timezone: value })}
      />
      <TextField
        id="tenant-bumpa-currency"
        label="Store currency"
        value={form.store_currency}
        maxLength={3}
        help="Three-letter code used to validate monetary data."
        onChange={(value) => update({ store_currency: value.toUpperCase() })}
      />
    </Modal>
  );
}

function ConfirmationDialog({
  view,
  action,
}: {
  view: TenantDetailView;
  action: Exclude<ConfirmedAction, null>;
}) {
  const tenantName = view.resource.data?.name ?? "this tenant";
  const copy = {
    sync: {
      title: "Queue Bumpa refresh?",
      body: `Queue a 30-day Bumpa refresh for ${tenantName}? This uses provider capacity and is audit logged.`,
      label: "Queue refresh",
    },
    restart: {
      title: "Restart Hermes profile?",
      body: `Restart only ${tenantName}'s Hermes profile? Active requests may briefly retry.`,
      label: "Restart profile",
    },
    suspend: {
      title: "Suspend tenant?",
      body: `Suspend ${tenantName}? Its members will lose workspace access.`,
      label: "Suspend tenant",
    },
  }[action];
  const busy = view.saving || view.syncing || view.restarting;
  return (
    <Modal
      title={copy.title}
      onClose={() => !busy && view.setConfirmation(null)}
      actions={
        <>
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={() => view.setConfirmation(null)}
          >
            Cancel
          </button>
          <button
            type="button"
            className={
              action === "sync"
                ? "button button-primary"
                : "button button-danger"
            }
            disabled={busy}
            onClick={() => void view.confirmAction()}
          >
            {busy ? "Working…" : copy.label}
          </button>
        </>
      }
    >
      <p>{copy.body}</p>
    </Modal>
  );
}

function EditorActions({
  busy,
  disabled,
  onCancel,
  onSave,
  saveLabel,
}: {
  busy: boolean;
  disabled: boolean;
  onCancel: () => void;
  onSave: () => Promise<void>;
  saveLabel: string;
}) {
  return (
    <>
      <button
        type="button"
        className="button button-secondary"
        disabled={busy}
        onClick={onCancel}
      >
        Cancel
      </button>
      <button
        type="button"
        className="button button-primary"
        disabled={busy || disabled}
        onClick={() => void onSave()}
      >
        {busy ? "Saving…" : saveLabel}
      </button>
    </>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  help,
  ...inputProps
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  help?: string;
} & Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "id" | "value" | "onChange"
>) {
  const helpId = help ? `${id}-help` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        {...inputProps}
        id={id}
        className="input"
        value={value}
        aria-describedby={helpId}
        onChange={(event) => onChange(event.target.value)}
      />
      {help && (
        <span className="field-help" id={helpId}>
          {help}
        </span>
      )}
    </div>
  );
}
