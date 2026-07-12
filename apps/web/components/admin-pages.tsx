"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";
import {
  durationBetween,
  formatDate,
  maskPhone,
  titleCase,
  type AsyncJob,
  type AsyncJobReplayReason,
  type AuditEvent,
  type PlatformAdmin,
  type SyncRun,
  type SystemError,
  type Tenant,
  type UsageEvent,
} from "@/lib/platform-data";
import {
  previewAudits,
  previewDeadLetterJobs,
  previewErrors,
  previewSyncRuns,
  previewTenants,
  previewUsage,
} from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import {
  Badge,
  Card,
  Chart,
  Filters,
  Metric,
  Modal,
  PageHeader,
  StatePanel,
  Toast,
} from "./ui";

function ResourceState({
  status,
  error,
  onRetry,
  empty,
}: {
  status: "loading" | "ready" | "error";
  error: string | null;
  onRetry: () => Promise<void>;
  empty?: string;
}) {
  if (status === "loading") return <StatePanel type="loading" />;
  if (status === "error")
    return (
      <StatePanel
        type="error"
        description={error ?? undefined}
        action={
          <button
            className="button button-secondary"
            onClick={() => void onRetry()}
          >
            Try again
          </button>
        }
      />
    );
  if (empty)
    return (
      <StatePanel
        type="empty"
        title={empty}
        description="The API returned no records for this view."
      />
    );
  return null;
}

function tenantLabel(tenant: Tenant): string {
  return [titleCase(tenant.business_category), tenant.city]
    .filter((value) => value && value !== "Not available")
    .join(" · ");
}

function percent(numerator: number, denominator: number): string {
  return denominator ? `${Math.round((numerator / denominator) * 100)}%` : "—";
}

function usageChart(events: UsageEvent[]): {
  labels: string[];
  values: number[];
} {
  const counts = new Map<string, number>();
  for (const event of events) {
    const day = event.created_at.slice(5, 10);
    counts.set(day, (counts.get(day) ?? 0) + 1);
  }
  const rows = [...counts.entries()].sort().slice(-12);
  const max = Math.max(1, ...rows.map(([, value]) => value));
  return {
    labels: rows.map(([label]) => label),
    values: rows.map(([, value]) =>
      Math.max(8, Math.round((value / max) * 100)),
    ),
  };
}

export function AdminOverview() {
  const tenantResource = useApiResource<Tenant[]>(
    "/admin/tenants",
    previewTenants,
  );
  const syncResource = useApiResource<SyncRun[]>(
    "/admin/system/sync-runs",
    previewSyncRuns,
  );
  const errorResource = useApiResource<SystemError[]>(
    "/admin/system/errors",
    previewErrors,
  );
  const usageResource = useApiResource<UsageEvent[]>(
    "/admin/usage",
    previewUsage,
  );
  const tenants = tenantResource.data ?? [];
  const runs = syncResource.data ?? [];
  const errors = errorResource.data ?? [];
  const usage = usageResource.data ?? [];
  const overviewResources = [
    tenantResource,
    syncResource,
    errorResource,
    usageResource,
  ];
  const overviewStatus = overviewResources.some(
    (resource) => resource.status === "error",
  )
    ? "error"
    : overviewResources.some((resource) => resource.status === "loading")
      ? "loading"
      : "ready";
  const overviewSource = overviewResources.some(
    (resource) => resource.source === "demo",
  )
    ? "demo"
    : overviewResources.every((resource) => resource.source === "live")
      ? "live"
      : null;
  const overviewError = overviewResources
    .map((resource) => resource.error)
    .filter(Boolean)
    .join("; ");
  const chart = usageChart(usage);
  const successful = runs.filter(
    (run) => run.status.toLowerCase() === "success",
  ).length;
  const consented = tenants.filter(
    (tenant) => tenant.research_consent_status === "granted",
  ).length;
  const latestRun = new Map<string, SyncRun>();
  for (const run of runs) {
    if (run.tenant_id && !latestRun.has(run.tenant_id))
      latestRun.set(run.tenant_id, run);
  }

  return (
    <AppShell surface="admin" title="Operations overview">
      <PageHeader
        title="Platform operations"
        description="Current tenant, sync, usage, and error evidence from the platform APIs."
        actions={
          <Link className="button button-primary" href="/admin/onboarding">
            ＋ Onboard SME
          </Link>
        }
      />
      <LiveDataBanner
        label="operations datasets"
        source={overviewSource}
        status={overviewStatus}
        error={overviewError || null}
      />
      {tenantResource.status !== "ready" ? (
        <ResourceState
          status={tenantResource.status}
          error={tenantResource.error}
          onRetry={tenantResource.reload}
        />
      ) : (
        <>
          <div className="grid grid-4">
            <Metric
              label="Active SMEs"
              value={String(
                tenants.filter((tenant) => tenant.status === "active").length,
              )}
              note={`${consented} consented to research`}
            />
            <Metric
              label="Recorded usage events"
              value={
                usageResource.status === "ready"
                  ? usage.length.toLocaleString()
                  : "—"
              }
              note="Latest 100 events returned by the API"
            />
            <Metric
              label="Successful syncs"
              value={
                syncResource.status === "ready"
                  ? percent(successful, runs.length)
                  : "—"
              }
              note={`${runs.length} recent run${runs.length === 1 ? "" : "s"}`}
            />
            <Metric
              label="Open system errors"
              value={
                errorResource.status === "ready" ? String(errors.length) : "—"
              }
              note="Latest redacted error records"
            />
          </div>
          <div className="grid grid-2" style={{ marginTop: 18 }}>
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>Usage activity</h2>
                  <p>Relative event volume on dates returned by the API.</p>
                </div>
              </div>
              {usageResource.status === "ready" && chart.values.length ? (
                <Chart values={chart.values} labels={chart.labels} />
              ) : usageResource.status === "error" ? (
                <p className="table-secondary">
                  Usage could not be loaded: {usageResource.error}
                </p>
              ) : (
                <p className="table-secondary">
                  No usage events have been recorded yet.
                </p>
              )}
            </Card>
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>System attention</h2>
                  <p>Newest redacted errors returned by operations.</p>
                </div>
                <Link className="table-action" href="/admin/errors">
                  View all
                </Link>
              </div>
              {errors.length ? (
                <div className="timeline">
                  {errors.slice(0, 3).map((error) => (
                    <div className="timeline-item" key={error.id}>
                      <strong>{titleCase(error.service)}</strong>
                      <p>{error.message}</p>
                      <Badge>{titleCase(error.severity)}</Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="table-secondary">
                  No system errors were returned.
                </p>
              )}
            </Card>
          </div>
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Tenant health</h2>
                <p>Status and latest sync evidence for each workspace.</p>
              </div>
              <Link className="table-action" href="/admin/tenants">
                All tenants →
              </Link>
            </div>
            {tenants.length ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Tenant</th>
                      <th>Status</th>
                      <th>Latest sync</th>
                      <th>Consent</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tenants.slice(0, 6).map((tenant) => {
                      const run = latestRun.get(tenant.id);
                      return (
                        <tr key={tenant.id}>
                          <td>
                            <Link
                              className="table-primary"
                              href={`/admin/tenants/${tenant.id}`}
                            >
                              {tenant.name}
                            </Link>
                            <div className="table-secondary">
                              {tenantLabel(tenant)}
                            </div>
                          </td>
                          <td>
                            <Badge>{titleCase(tenant.status)}</Badge>
                          </td>
                          <td>
                            {run ? (
                              <>
                                <Badge>{titleCase(run.status)}</Badge> ·{" "}
                                {formatDate(run.started_at)}
                              </>
                            ) : (
                              "No run"
                            )}
                          </td>
                          <td>
                            <Badge>
                              {titleCase(tenant.research_consent_status)}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="table-secondary">No tenants have been onboarded.</p>
            )}
          </Card>
        </>
      )}
    </AppShell>
  );
}

export function TenantList() {
  const resource = useApiResource<Tenant[]>("/admin/tenants", previewTenants);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const rows = useMemo(
    () =>
      (resource.data ?? []).filter((tenant) => {
        const matchesText = `${tenant.name} ${tenant.slug} ${tenant.city ?? ""}`
          .toLowerCase()
          .includes(search.toLowerCase());
        return matchesText && (status === "all" || tenant.status === status);
      }),
    [resource.data, search, status],
  );
  return (
    <AppShell surface="admin" title="Tenants">
      <PageHeader
        title="SME tenants"
        description="Onboard, monitor, and safely manage every isolated business workspace."
        actions={
          <Link className="button button-primary" href="/admin/onboarding">
            ＋ Onboard SME
          </Link>
        }
      />
      <LiveDataBanner
        label="tenants"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      {resource.status !== "ready" ? (
        <ResourceState
          status={resource.status}
          error={resource.error}
          onRetry={resource.reload}
        />
      ) : !resource.data?.length ? (
        <ResourceState
          status="ready"
          error={null}
          onRetry={resource.reload}
          empty="No SMEs onboarded"
        />
      ) : (
        <>
          <Filters search={search} setSearch={setSearch}>
            <select
              className="filter-select"
              aria-label="Filter by status"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
              <option value="archived">Archived</option>
            </select>
          </Filters>
          {rows.length ? (
            <section className="card table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Business</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th>Location</th>
                    <th>Consent</th>
                    <th>Created</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((tenant) => (
                    <tr key={tenant.id}>
                      <td>
                        <div className="table-primary">{tenant.name}</div>
                        <div className="table-secondary">{tenant.slug}</div>
                      </td>
                      <td>{titleCase(tenant.business_category)}</td>
                      <td>
                        <Badge>{titleCase(tenant.status)}</Badge>
                      </td>
                      <td>
                        {[tenant.city, tenant.country]
                          .filter(Boolean)
                          .join(", ") || "Not set"}
                      </td>
                      <td>
                        <Badge>
                          {titleCase(tenant.research_consent_status)}
                        </Badge>
                      </td>
                      <td>{formatDate(tenant.created_at)}</td>
                      <td>
                        <Link
                          className="table-action"
                          href={`/admin/tenants/${tenant.id}`}
                        >
                          Open →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ) : (
            <StatePanel
              type="empty"
              title="No matching tenants"
              description="Clear or adjust the filters to see other workspaces."
            />
          )}
        </>
      )}
    </AppShell>
  );
}

export function TenantDetail({ id }: { id: string }) {
  const demoTenant = useMemo(
    () =>
      previewTenants.find((tenant) => tenant.id === id) ?? previewTenants[0],
    [id],
  );
  const resource = useApiResource<Tenant>(`/admin/tenants/${id}`, demoTenant);
  const auditResource = useApiResource<AuditEvent[]>(
    "/admin/audit",
    previewAudits,
  );
  const [tab, setTab] = useState("Overview");
  const [toast, setToast] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [saving, setSaving] = useState(false);
  const tenant = resource.data;
  const suspend = async () => {
    if (!tenant || resource.source !== "live") return;
    if (
      !window.confirm(
        `Suspend ${tenant.name}? Its members will lose workspace access.`,
      )
    )
      return;
    setSaving(true);
    setMutationError("");
    try {
      const updated = await apiRequest<Tenant>(`/admin/tenants/${tenant.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "suspended" }),
      });
      resource.replace(updated);
      setToast(`${tenant.name} is suspended.`);
    } catch (reason) {
      setMutationError(
        reason instanceof Error
          ? reason.message
          : "The tenant could not be suspended.",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell surface="admin" title={tenant?.name ?? "Tenant detail"}>
      <PageHeader
        title={tenant?.name ?? "Tenant detail"}
        description={
          tenant
            ? tenantLabel(tenant) || "Business workspace"
            : "Loading business workspace."
        }
        actions={
          <>
            <button
              className="button button-secondary"
              disabled
              title="No operator-scoped sync endpoint is available."
            >
              ↻ Trigger sync
            </button>
            <button
              className="button button-danger"
              disabled={
                !tenant ||
                resource.source !== "live" ||
                tenant.status === "suspended" ||
                saving
              }
              onClick={() => void suspend()}
              title={
                resource.source !== "live"
                  ? "Destructive actions require live API data."
                  : undefined
              }
            >
              {saving ? "Suspending…" : "Suspend tenant"}
            </button>
          </>
        }
      />
      <LiveDataBanner
        label="tenant record"
        source={resource.source}
        status={resource.status}
        error={resource.error}
      />
      {mutationError && (
        <div className="alert alert-danger" role="alert">
          {mutationError}
        </div>
      )}
      {resource.status !== "ready" || !tenant ? (
        <ResourceState
          status={resource.status}
          error={resource.error}
          onRetry={resource.reload}
        />
      ) : (
        <>
          <div
            className="tabs"
            role="tablist"
            aria-label="Tenant detail sections"
          >
            {[
              "Overview",
              "People & phones",
              "Bumpa",
              "Hermes",
              "Audit log",
            ].map((name) => (
              <button
                role="tab"
                aria-selected={tab === name}
                className={`tab ${tab === name ? "active" : ""}`}
                key={name}
                onClick={() => setTab(name)}
              >
                {name}
              </button>
            ))}
          </div>
          {tab === "Overview" && (
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
                  [
                    "Research consent",
                    titleCase(tenant.research_consent_status),
                  ],
                  ["Created", formatDate(tenant.created_at)],
                ].map(([label, value]) => (
                  <div className="detail-row" key={label}>
                    <span className="detail-label">{label}</span>
                    <span className="detail-value">{value}</span>
                  </div>
                ))}
              </Card>
              <Card padded>
                <div className="card-head">
                  <div>
                    <h2>Readiness evidence</h2>
                    <p>Only evidence exposed by the current API is shown.</p>
                  </div>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Tenant record</span>
                  <Badge>Complete</Badge>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Active access</span>
                  <Badge>
                    {tenant.status === "active" ? "Complete" : "Unavailable"}
                  </Badge>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Research consent</span>
                  <Badge>{titleCase(tenant.research_consent_status)}</Badge>
                </div>
                <div
                  className="alert alert-info"
                  style={{ marginTop: 16, marginBottom: 0 }}
                >
                  Membership, Bumpa, and Hermes readiness require dedicated
                  operator read endpoints before they can be asserted here.
                </div>
              </Card>
            </div>
          )}
          {tab === "People & phones" && (
            <StatePanel
              type="empty"
              title="Cross-tenant identity detail is not exposed"
              description="The operator API can create users and phones, but it does not yet provide a tenant-scoped identity list. No preview identities are shown as live."
              action={
                <button className="button button-secondary" disabled>
                  ＋ Add user unavailable
                </button>
              }
            />
          )}
          {tab === "Bumpa" && (
            <StatePanel
              type="empty"
              title="Bumpa status is not exposed to operators"
              description="Connection credentials can be written during onboarding, but the API has no operator-scoped status endpoint for this tenant."
              action={
                <button className="button button-secondary" disabled>
                  Replace API key unavailable
                </button>
              }
            />
          )}
          {tab === "Hermes" && (
            <StatePanel
              type="empty"
              title="Hermes status is not exposed to operators"
              description="A profile can be provisioned, but runtime health and restart endpoints are not available yet."
              action={
                <button className="button button-secondary" disabled>
                  Restart unavailable
                </button>
              }
            />
          )}
          {tab === "Audit log" &&
            (auditResource.status !== "ready" ? (
              <ResourceState
                status={auditResource.status}
                error={auditResource.error}
                onRetry={auditResource.reload}
              />
            ) : (
              <Card padded>
                <div className="timeline">
                  {(auditResource.data ?? [])
                    .filter((event) => event.tenant_id === tenant.id)
                    .map((event) => (
                      <div className="timeline-item" key={event.id}>
                        <strong>{event.action}</strong>
                        <p>
                          {titleCase(event.resource_type)} ·{" "}
                          {formatDate(event.created_at)}
                        </p>
                      </div>
                    ))}
                  {!(auditResource.data ?? []).some(
                    (event) => event.tenant_id === tenant.id,
                  ) && (
                    <p className="table-secondary">
                      No audit events were returned for this tenant.
                    </p>
                  )}
                </div>
              </Card>
            ))}
        </>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

export function UserList() {
  const [search, setSearch] = useState("");
  const admins = useApiResource<PlatformAdmin[]>("/admin/platform-admins");
  const session = useApiResource<{
    user: { id: string };
    platform_roles: string[];
    memberships: Array<{
      id: string;
      tenant_id: string;
      role: string;
      status: string;
    }>;
    current_tenant_id: string | null;
  }>("/auth/me");
  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [nameError, setNameError] = useState("");
  const [phoneError, setPhoneError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingRevoke, setPendingRevoke] = useState<PlatformAdmin | null>(
    null,
  );
  const [toast, setToast] = useState("");
  const rows = useMemo(
    () =>
      (admins.data ?? []).filter((admin) =>
        `${admin.name ?? ""} ${admin.phone_e164} ${admin.platform_roles.join(" ")}`
          .toLowerCase()
          .includes(search.trim().toLowerCase()),
      ),
    [admins.data, search],
  );

  const closeAdd = () => {
    if (busy) return;
    setAddOpen(false);
    setName("");
    setPhone("");
    setNameError("");
    setPhoneError("");
    setMutationError("");
  };

  const addAdmin = async () => {
    const nextNameError = name.trim() ? "" : "Enter the administrator's name.";
    const nextPhoneError = /^\+[1-9]\d{7,14}$/.test(phone.trim())
      ? ""
      : "Use E.164 format, for example +2348012345678.";
    setNameError(nextNameError);
    setPhoneError(nextPhoneError);
    if (nextNameError || nextPhoneError) return;
    setBusy(true);
    setMutationError("");
    try {
      await apiRequest<PlatformAdmin>("/admin/platform-admins", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          phone_e164: phone.trim(),
          role: "operator",
        }),
      });
      await admins.reload();
      const addedName = name.trim();
      setAddOpen(false);
      setName("");
      setPhone("");
      setNameError("");
      setPhoneError("");
      setMutationError("");
      setToast(`${addedName} can now administer tenant mappings.`);
    } catch (reason) {
      setMutationError(
        reason instanceof Error
          ? reason.message
          : "The platform administrator could not be added.",
      );
    } finally {
      setBusy(false);
    }
  };

  const revokeAdmin = async () => {
    if (!pendingRevoke) return;
    setBusy(true);
    setMutationError("");
    try {
      await apiRequest<void>(
        `/admin/platform-admins/${pendingRevoke.user_id}`,
        { method: "DELETE" },
      );
      const revokedName = pendingRevoke.name?.trim() || "The administrator";
      await admins.reload();
      setPendingRevoke(null);
      setToast(`${revokedName}'s platform access was revoked.`);
    } catch (reason) {
      setMutationError(
        reason instanceof Error
          ? reason.message
          : "Platform access could not be revoked.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell surface="admin" title="Administrators">
      <PageHeader
        title="Platform administrators"
        description="Grant trusted operators access to onboard businesses and manage tenant mappings."
        actions={
          <button
            className="button button-primary"
            disabled={admins.source !== "live" || admins.status !== "ready"}
            onClick={() => {
              setMutationError("");
              setAddOpen(true);
            }}
          >
            ＋ Add administrator
          </button>
        }
      />
      <LiveDataBanner
        label="platform administrators"
        source={admins.source}
        status={admins.status}
        count={admins.data?.length}
        error={admins.error}
      />
      <div className="alert alert-info">
        <div>
          <strong>Platform access is separate from store access.</strong>
          <div>
            Administrators can manage every tenant mapping. They may also hold a
            normal owner or team membership in a specific workspace.
          </div>
        </div>
      </div>
      {admins.status === "loading" ? (
        <StatePanel type="loading" />
      ) : admins.status === "error" ? (
        <StatePanel
          type="error"
          title="Administrators could not be loaded"
          description={admins.error ?? undefined}
          action={
            <button
              className="button button-secondary"
              onClick={() => void admins.reload()}
            >
              Try again
            </button>
          }
        />
      ) : !admins.data?.length ? (
        <StatePanel
          type="empty"
          title="No platform administrators returned"
          description="Add a trusted administrator to begin managing tenant mappings."
          action={
            <button
              className="button button-primary"
              onClick={() => setAddOpen(true)}
            >
              Add administrator
            </button>
          }
        />
      ) : (
        <>
          <Filters search={search} setSearch={setSearch} />
          {rows.length ? (
            <section className="card table-wrap">
              <table className="data-table admin-directory-table">
                <thead>
                  <tr>
                    <th>Administrator</th>
                    <th>Phone</th>
                    <th>Access</th>
                    <th>Status</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((admin) => {
                    const isCurrent = session.data?.user.id === admin.user_id;
                    const isSuperadmin =
                      admin.platform_roles.includes("superadmin");
                    const displayName = admin.name?.trim() || "Unnamed admin";
                    return (
                      <tr key={admin.user_id}>
                        <td>
                          <div className="admin-identity">
                            <span className="avatar" aria-hidden="true">
                              {displayName
                                .split(/\s+/)
                                .map((part) => part[0])
                                .slice(0, 2)
                                .join("")
                                .toUpperCase()}
                            </span>
                            <span>
                              <span className="table-primary">
                                {displayName}
                              </span>
                              <span className="table-secondary">
                                {isCurrent
                                  ? "Your account"
                                  : "Platform account"}
                              </span>
                            </span>
                          </div>
                        </td>
                        <td>
                          <span
                            aria-label={`Phone ending ${admin.phone_e164.slice(-4)}`}
                          >
                            {maskPhone(admin.phone_e164)}
                          </span>
                        </td>
                        <td>
                          <div className="admin-role-list">
                            {admin.platform_roles.map((role) => (
                              <Badge key={role}>{titleCase(role)}</Badge>
                            ))}
                          </div>
                        </td>
                        <td>
                          <Badge>{titleCase(admin.status)}</Badge>
                        </td>
                        <td className="admin-directory-action">
                          {session.status !== "ready" ? (
                            <span className="admin-protected-label">
                              Checking account…
                            </span>
                          ) : isCurrent ? (
                            <span className="admin-protected-label">
                              Current administrator
                            </span>
                          ) : isSuperadmin ? (
                            <span className="admin-protected-label">
                              Superadmin protected
                            </span>
                          ) : (
                            <button
                              className="button button-danger button-small"
                              disabled={busy || admin.status !== "active"}
                              onClick={() => {
                                setMutationError("");
                                setPendingRevoke(admin);
                              }}
                              aria-label={`Revoke ${displayName}'s platform access`}
                            >
                              Revoke access
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          ) : (
            <StatePanel
              type="empty"
              title="No matching administrators"
              description="Clear or adjust your search to see other administrators."
              action={
                <button
                  className="button button-secondary"
                  onClick={() => setSearch("")}
                >
                  Clear search
                </button>
              }
            />
          )}
        </>
      )}
      {addOpen && (
        <Modal
          title="Add a platform administrator"
          onClose={closeAdd}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={closeAdd}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                disabled={busy}
                aria-busy={busy}
                onClick={() => void addAdmin()}
              >
                {busy ? "Granting access…" : "Grant administrator access"}
              </button>
            </>
          }
        >
          <p className="modal-intro">
            This grants cross-tenant operator access. Store memberships remain
            separate and can be assigned to the same person when needed.
          </p>
          {mutationError && (
            <div className="alert alert-danger" role="alert">
              {mutationError}
            </div>
          )}
          <div className="field">
            <label htmlFor="platform-admin-name">Full name</label>
            <input
              id="platform-admin-name"
              className={`input ${nameError ? "input-error" : ""}`}
              value={name}
              autoComplete="name"
              aria-invalid={Boolean(nameError)}
              aria-describedby={
                nameError ? "platform-admin-name-error" : undefined
              }
              onChange={(event) => {
                setName(event.target.value);
                if (nameError) setNameError("");
              }}
            />
            {nameError && (
              <span className="field-error" id="platform-admin-name-error">
                {nameError}
              </span>
            )}
          </div>
          <div className="field">
            <label htmlFor="platform-admin-phone">WhatsApp phone number</label>
            <input
              id="platform-admin-phone"
              type="tel"
              inputMode="tel"
              className={`input ${phoneError ? "input-error" : ""}`}
              placeholder="+2348012345678"
              value={phone}
              autoComplete="tel"
              aria-invalid={Boolean(phoneError)}
              aria-describedby={
                phoneError
                  ? "platform-admin-phone-help platform-admin-phone-error"
                  : "platform-admin-phone-help"
              }
              onChange={(event) => {
                setPhone(event.target.value);
                if (phoneError) setPhoneError("");
              }}
            />
            <span className="field-help" id="platform-admin-phone-help">
              Use the person&apos;s verified number in international E.164
              format.
            </span>
            {phoneError && (
              <span className="field-error" id="platform-admin-phone-error">
                {phoneError}
              </span>
            )}
          </div>
          <div className="admin-grant-summary" aria-label="Access to grant">
            <span className="admin-grant-icon" aria-hidden="true">
              ◎
            </span>
            <span>
              <strong>Platform operator</strong>
              <small>Tenant onboarding, mapping, and operations access</small>
            </span>
          </div>
        </Modal>
      )}
      {pendingRevoke && (
        <Modal
          title="Revoke platform access?"
          onClose={() => {
            if (!busy) {
              setPendingRevoke(null);
              setMutationError("");
            }
          }}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => {
                  setPendingRevoke(null);
                  setMutationError("");
                }}
              >
                Keep access
              </button>
              <button
                className="button button-danger"
                disabled={busy}
                aria-busy={busy}
                onClick={() => void revokeAdmin()}
              >
                {busy ? "Revoking…" : "Revoke platform access"}
              </button>
            </>
          }
        >
          {mutationError && (
            <div className="alert alert-danger" role="alert">
              {mutationError}
            </div>
          )}
          <p className="modal-intro">
            <strong>
              {pendingRevoke.name?.trim() || "This administrator"}
            </strong>{" "}
            will no longer be able to onboard businesses or change tenant
            mappings. Any store membership they hold remains unchanged.
          </p>
          <div className="alert alert-warning" style={{ marginBottom: 0 }}>
            This change takes effect immediately and is recorded in the audit
            trail.
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

export function SyncList() {
  const runs = useApiResource<SyncRun[]>(
    "/admin/system/sync-runs",
    previewSyncRuns,
  );
  const tenants = useApiResource<Tenant[]>("/admin/tenants", previewTenants);
  const names = new Map(
    (tenants.data ?? []).map((tenant) => [tenant.id, tenant.name]),
  );
  const successCount = (runs.data ?? []).filter(
    (run) => run.status === "success",
  ).length;
  return (
    <AppShell surface="admin" title="Sync runs">
      <PageHeader
        title="Bumpa sync runs"
        description="Monitor freshness and upstream failures from recorded sync runs."
        actions={
          <button
            className="button button-primary"
            disabled
            title="Choose a tenant after an operator-scoped trigger endpoint is added."
          >
            ↻ Trigger sync unavailable
          </button>
        }
      />
      <LiveDataBanner
        label="sync runs"
        source={runs.source}
        status={runs.status}
        count={runs.data?.length}
        error={runs.error}
      />
      {runs.status !== "ready" ? (
        <ResourceState
          status={runs.status}
          error={runs.error}
          onRetry={runs.reload}
        />
      ) : !runs.data?.length ? (
        <ResourceState
          status="ready"
          error={null}
          onRetry={runs.reload}
          empty="No sync runs yet"
        />
      ) : (
        <>
          <div className="grid grid-3">
            <Metric
              label="Success rate"
              value={percent(successCount, runs.data.length)}
              note="Recent API window"
            />
            <Metric
              label="Running now"
              value={String(
                runs.data.filter((run) => run.status === "running").length,
              )}
            />
            <Metric
              label="Failed or partial"
              value={String(
                runs.data.filter((run) =>
                  ["failed", "partial"].includes(run.status),
                ).length,
              )}
            />
          </div>
          <section className="card table-wrap" style={{ marginTop: 18 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Tenant</th>
                  <th>Date range</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Datasets</th>
                  <th>Status</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((run) => (
                  <tr key={run.id}>
                    <td className="table-primary">
                      {run.tenant_id
                        ? (names.get(run.tenant_id) ??
                          run.tenant_id.slice(0, 8))
                        : "Unknown"}
                    </td>
                    <td>
                      {run.requested_from && run.requested_to
                        ? `${run.requested_from} – ${run.requested_to}`
                        : "Not recorded"}
                    </td>
                    <td>{formatDate(run.started_at)}</td>
                    <td>{durationBetween(run.started_at, run.finished_at)}</td>
                    <td>
                      {run.dataset_results
                        ? Object.keys(run.dataset_results).length
                        : "—"}
                    </td>
                    <td>
                      <Badge>{titleCase(run.status)}</Badge>
                    </td>
                    <td>{run.error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </AppShell>
  );
}

export function ErrorList() {
  const errorResource = useApiResource<SystemError[]>(
    "/admin/system/errors",
    previewErrors,
  );
  const jobResource = useApiResource<AsyncJob[]>(
    "/admin/system/jobs?status=dead_letter",
    previewDeadLetterJobs,
  );
  const [search, setSearch] = useState("");
  const [selectedJob, setSelectedJob] = useState<AsyncJob | null>(null);
  const [replayReason, setReplayReason] = useState<AsyncJobReplayReason>(
    "operator_verified_safe_retry",
  );
  const [replayPending, setReplayPending] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const rows = (errorResource.data ?? []).filter((error) =>
    `${error.service} ${error.message}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );

  async function replayJob() {
    if (!selectedJob || replayPending) return;
    setReplayPending(true);
    setReplayError(null);
    try {
      await apiRequest<AsyncJob>(
        `/admin/system/jobs/${selectedJob.id}/replay`,
        {
          method: "POST",
          body: JSON.stringify({ reason: replayReason }),
        },
      );
      await Promise.all([jobResource.reload(), errorResource.reload()]);
      setSelectedJob(null);
      setToast("Job queued for a fresh, audited attempt.");
    } catch (error) {
      setReplayError(
        error instanceof Error
          ? error.message
          : "The job could not be replayed. Try again.",
      );
    } finally {
      setReplayPending(false);
    }
  }

  return (
    <AppShell surface="admin" title="Failure recovery">
      <PageHeader
        title="Failure recovery"
        description="Triage scrubbed operational signals and safely replay terminal asynchronous work."
      />
      <LiveDataBanner
        label="dead-letter jobs"
        source={jobResource.source}
        status={jobResource.status}
        count={jobResource.data?.length}
        error={jobResource.error}
      />
      <div className="alert alert-info">
        Job payloads, results, credentials, worker identifiers, and raw errors
        are never returned to this screen. Every replay requires a controlled
        reason and is recorded in the platform audit trail.
      </div>
      <section style={{ marginTop: 20 }} aria-labelledby="dead-letter-heading">
        <div className="section-title">
          <div>
            <h2 id="dead-letter-heading">Needs operator action</h2>
            <p>Terminal jobs stay here until the underlying cause is fixed.</p>
          </div>
          {jobResource.status === "ready" && (
            <Badge tone={jobResource.data?.length ? "danger" : "success"}>
              {jobResource.data?.length ?? 0} open
            </Badge>
          )}
        </div>
        {jobResource.status !== "ready" ? (
          <ResourceState
            status={jobResource.status}
            error={jobResource.error}
            onRetry={jobResource.reload}
          />
        ) : !jobResource.data?.length ? (
          <ResourceState
            status="ready"
            error={null}
            onRetry={jobResource.reload}
            empty="No terminal jobs need recovery"
          />
        ) : (
          <div className="grid">
            {jobResource.data.map((job) => (
              <Card padded key={job.id}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 16,
                    alignItems: "flex-start",
                    flexWrap: "wrap",
                  }}
                >
                  <div>
                    <div
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                        marginBottom: 10,
                        flexWrap: "wrap",
                      }}
                    >
                      <Badge tone="danger">Dead letter</Badge>
                      <span className="tag">{titleCase(job.kind)}</span>
                    </div>
                    <strong>{titleCase(job.failure_category)}</strong>
                    <p className="table-secondary">
                      Attempted {job.attempts} of {job.max_attempts} times ·
                      finished {formatDate(job.finished_at)}
                    </p>
                    <p className="table-secondary">
                      Tenant {job.tenant_id?.slice(0, 8) ?? "platform"} · Job{" "}
                      {job.id.slice(0, 12)}
                    </p>
                  </div>
                  <button
                    className="button button-primary button-small"
                    onClick={() => {
                      setReplayError(null);
                      setReplayReason("operator_verified_safe_retry");
                      setSelectedJob(job);
                    }}
                    disabled={!job.replayable || jobResource.source !== "live"}
                  >
                    {jobResource.source === "demo"
                      ? "Replay unavailable in preview"
                      : "Review replay"}
                  </button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section
        style={{ marginTop: 28 }}
        aria-labelledby="system-errors-heading"
      >
        <div className="section-title">
          <div>
            <h2 id="system-errors-heading">Operational signals</h2>
            <p>Scrubbed events for investigation and trend monitoring.</p>
          </div>
        </div>
        {errorResource.status !== "ready" ? (
          <ResourceState
            status={errorResource.status}
            error={errorResource.error}
            onRetry={errorResource.reload}
          />
        ) : !errorResource.data?.length ? (
          <ResourceState
            status="ready"
            error={null}
            onRetry={errorResource.reload}
            empty="No system errors recorded"
          />
        ) : (
          <>
            <Filters search={search} setSearch={setSearch} />
            <div className="grid">
              {rows.map((error) => (
                <Card padded key={error.id}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 16,
                      alignItems: "flex-start",
                    }}
                  >
                    <div>
                      <div
                        style={{
                          display: "flex",
                          gap: 8,
                          alignItems: "center",
                          marginBottom: 10,
                        }}
                      >
                        <Badge>{titleCase(error.severity)}</Badge>
                        <span className="tag">{titleCase(error.service)}</span>
                      </div>
                      <strong>{error.message}</strong>
                      <p className="table-secondary">
                        Recorded {formatDate(error.created_at)} ·{" "}
                        {error.id.slice(0, 12)}
                      </p>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
                    <button
                      className="button button-secondary button-small"
                      disabled
                    >
                      Inspect unavailable
                    </button>
                    <button
                      className="button button-ghost button-small"
                      disabled
                    >
                      Resolve unavailable
                    </button>
                  </div>
                </Card>
              ))}
              {!rows.length && (
                <StatePanel
                  type="empty"
                  title="No matching errors"
                  description="Try a different search term."
                />
              )}
            </div>
          </>
        )}
      </section>
      {selectedJob && (
        <Modal
          title="Replay terminal job"
          onClose={() => {
            if (!replayPending) setSelectedJob(null);
          }}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setSelectedJob(null)}
                disabled={replayPending}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                onClick={() => void replayJob()}
                disabled={replayPending}
                aria-busy={replayPending}
              >
                {replayPending ? "Queueing…" : "Confirm audited replay"}
              </button>
            </>
          }
        >
          <p>
            Replay <strong>{titleCase(selectedJob.kind)}</strong> only after the
            failure cause has been addressed. The original payload remains
            sealed in the worker database and is not shown here.
          </p>
          <label className="field" htmlFor="replay-reason">
            <span>Recovery reason</span>
            <select
              id="replay-reason"
              className="select"
              value={replayReason}
              onChange={(event) =>
                setReplayReason(event.target.value as AsyncJobReplayReason)
              }
              disabled={replayPending}
            >
              <option value="operator_verified_safe_retry">
                Operator verified a safe retry
              </option>
              <option value="configuration_corrected">
                Configuration corrected
              </option>
              <option value="dependency_recovered">Dependency recovered</option>
              <option value="transient_provider_recovered">
                Provider recovered
              </option>
              <option value="upstream_credentials_rotated">
                Upstream credentials rotated
              </option>
            </select>
          </label>
          {replayError && (
            <div className="alert alert-danger" role="alert">
              {replayError}
            </div>
          )}
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

function downloadUsage(events: UsageEvent[]) {
  const lines = [
    "id,tenant_id,event_name,created_at",
    ...events.map((event) =>
      [event.id, event.tenant_id ?? "", event.event_name, event.created_at]
        .map((value) => `"${String(value).replaceAll('"', '""')}"`)
        .join(","),
    ),
  ];
  const url = URL.createObjectURL(
    new Blob([lines.join("\n")], { type: "text/csv" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "bumpa-bestie-usage.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function UsageList() {
  const usage = useApiResource<UsageEvent[]>("/admin/usage", previewUsage);
  const tenants = useApiResource<Tenant[]>("/admin/tenants", previewTenants);
  const names = new Map(
    (tenants.data ?? []).map((tenant) => [tenant.id, tenant.name]),
  );
  const grouped = useMemo(() => {
    const map = new Map<
      string,
      { total: number; whatsapp: number; web: number }
    >();
    for (const event of usage.data ?? []) {
      const key = event.tenant_id ?? "platform";
      const row = map.get(key) ?? { total: 0, whatsapp: 0, web: 0 };
      row.total += 1;
      if (event.event_name.toLowerCase().includes("whatsapp"))
        row.whatsapp += 1;
      if (event.event_name.toLowerCase().includes("web")) row.web += 1;
      map.set(key, row);
    }
    return [...map.entries()];
  }, [usage.data]);
  return (
    <AppShell surface="admin" title="Usage">
      <PageHeader
        title="Usage and capacity"
        description="Recorded usage events grouped without inventing cost or active-user metrics."
        actions={
          <button
            className="button button-secondary"
            disabled={usage.status !== "ready" || !usage.data?.length}
            onClick={() => usage.data && downloadUsage(usage.data)}
          >
            ⇩ Export loaded CSV
          </button>
        }
      />
      <LiveDataBanner
        label="usage events"
        source={usage.source}
        status={usage.status}
        count={usage.data?.length}
        error={usage.error}
      />
      {usage.status !== "ready" ? (
        <ResourceState
          status={usage.status}
          error={usage.error}
          onRetry={usage.reload}
        />
      ) : !usage.data?.length ? (
        <ResourceState
          status="ready"
          error={null}
          onRetry={usage.reload}
          empty="No usage events recorded"
        />
      ) : (
        <>
          <div className="grid grid-3">
            <Metric
              label="Recorded events"
              value={usage.data.length.toLocaleString()}
              note="Latest API window"
            />
            <Metric
              label="WhatsApp events"
              value={String(
                usage.data.filter((event) =>
                  event.event_name.toLowerCase().includes("whatsapp"),
                ).length,
              )}
            />
            <Metric
              label="Web events"
              value={String(
                usage.data.filter((event) =>
                  event.event_name.toLowerCase().includes("web"),
                ).length,
              )}
            />
          </div>
          <section className="card table-wrap" style={{ marginTop: 18 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Tenant</th>
                  <th>Events</th>
                  <th>WhatsApp-labelled</th>
                  <th>Web-labelled</th>
                </tr>
              </thead>
              <tbody>
                {grouped.map(([tenantId, row]) => (
                  <tr key={tenantId}>
                    <td className="table-primary">
                      {names.get(tenantId) ??
                        (tenantId === "platform"
                          ? "Platform"
                          : tenantId.slice(0, 8))}
                    </td>
                    <td>{row.total}</td>
                    <td>{row.whatsapp}</td>
                    <td>{row.web}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <div className="alert alert-info">
            The current API records event names and timestamps. Token cost and
            active-user metrics are intentionally omitted because the API does
            not provide them.
          </div>
        </>
      )}
    </AppShell>
  );
}

type OnboardingForm = {
  name: string;
  slug: string;
  category: string;
  city: string;
  ownerName: string;
  ownerPhone: string;
  ownerEmail: string;
  phoneLabel: string;
  bumpaApiKey: string;
  bumpaScopeId: string;
};

type BumpaConnectionResult = {
  id: string;
  status: string;
  provider: string;
};

type HermesProfileResult = {
  id: string;
  profile_name: string;
  status: string;
};

export function Onboarding() {
  const steps = [
    "Business",
    "Owner",
    "WhatsApp identity",
    "Bumpa",
    "Hermes",
    "Review",
  ];
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<OnboardingForm>({
    name: "",
    slug: "",
    category: "",
    city: "",
    ownerName: "",
    ownerPhone: "",
    ownerEmail: "",
    phoneLabel: "Owner",
    bumpaApiKey: "",
    bumpaScopeId: "",
  });
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [owner, setOwner] = useState<{
    user_id: string;
    membership_id: string;
  } | null>(null);
  const [phoneCreated, setPhoneCreated] = useState(false);
  const [bumpaScopeType, setBumpaScopeType] = useState<
    "business_id" | "location_id"
  >("business_id");
  const [bumpaConnection, setBumpaConnection] =
    useState<BumpaConnectionResult | null>(null);
  const [hermesProfile, setHermesProfile] =
    useState<HermesProfileResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const update = (key: keyof OnboardingForm, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));
  const continueStep = async () => {
    setBusy(true);
    setError("");
    try {
      if (step === 0 && !tenant) {
        const created = await apiRequest<Tenant>("/admin/tenants", {
          method: "POST",
          body: JSON.stringify({
            slug: form.slug,
            name: form.name,
            business_category: form.category || null,
            country: "NG",
            city: form.city || null,
            timezone: "Africa/Lagos",
            currency_code: "NGN",
          }),
        });
        setTenant(created);
      }
      if (step === 1 && tenant && !owner) {
        const created = await apiRequest<{
          user_id: string;
          membership_id: string;
        }>(`/admin/tenants/${tenant.id}/users`, {
          method: "POST",
          body: JSON.stringify({
            name: form.ownerName,
            phone_e164: form.ownerPhone,
            email: form.ownerEmail || null,
            role: "owner",
          }),
        });
        setOwner(created);
      }
      if (step === 2 && tenant && owner && !phoneCreated) {
        await apiRequest(`/admin/tenants/${tenant.id}/phones`, {
          method: "POST",
          body: JSON.stringify({
            user_id: owner.user_id,
            phone_e164: form.ownerPhone,
            label: form.phoneLabel || "Owner",
          }),
        });
        setPhoneCreated(true);
      }
      if (step === 3 && tenant && !bumpaConnection) {
        const created = await apiRequest<BumpaConnectionResult>(
          `/admin/tenants/${tenant.id}/bumpa`,
          {
            method: "POST",
            body: JSON.stringify({
              api_key: form.bumpaApiKey,
              scope_type: bumpaScopeType,
              scope_id: form.bumpaScopeId,
              provider: "bumpa",
            }),
          },
        );
        setBumpaConnection(created);
        setForm((current) => ({ ...current, bumpaApiKey: "" }));
      }
      if (step === 4 && tenant && !hermesProfile) {
        const created = await apiRequest<HermesProfileResult>(
          `/admin/tenants/${tenant.id}/hermes-profile`,
          { method: "POST" },
        );
        setHermesProfile(created);
      }
      setStep((current) => Math.min(current + 1, steps.length - 1));
    } catch (reason) {
      if (step === 3) {
        setForm((current) => ({ ...current, bumpaApiKey: "" }));
      }
      setError(
        reason instanceof Error
          ? reason.message
          : "This onboarding step could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <AppShell surface="admin" title="Onboard SME">
      <PageHeader
        title="Onboard a new SME"
        description="Create the isolated tenant, owner membership, and approved identity through audited APIs."
        actions={
          <Link className="button button-secondary" href="/admin/tenants">
            Exit onboarding
          </Link>
        }
      />
      <div
        className="grid"
        style={{ gridTemplateColumns: "minmax(190px,.35fr) minmax(0,1fr)" }}
      >
        <Card padded>
          <div className="timeline">
            {steps.map((name, index) => (
              <div className="timeline-item" key={name}>
                <strong
                  style={{
                    color: index === step ? "var(--forest)" : undefined,
                  }}
                >
                  {name}
                </strong>
                <p>
                  {index < step
                    ? "Complete"
                    : index === step
                      ? "In progress"
                      : "Not started"}
                </p>
              </div>
            ))}
          </div>
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <span className="eyebrow">
                Step {step + 1} of {steps.length}
              </span>
              <h2 style={{ fontSize: 24, marginTop: 12 }}>{steps[step]}</h2>
            </div>
            <Badge>{step === steps.length - 1 ? "Ready" : "Draft"}</Badge>
          </div>
          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}
          {step === 0 && (
            <div className="grid grid-2">
              <div className="field">
                <label htmlFor="business-name">Business name</label>
                <input
                  id="business-name"
                  className="input"
                  required
                  value={form.name}
                  onChange={(event) => update("name", event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="business-slug">Slug</label>
                <input
                  id="business-slug"
                  className="input"
                  required
                  pattern="[a-z0-9-]+"
                  value={form.slug}
                  onChange={(event) =>
                    update(
                      "slug",
                      event.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9-]/g, ""),
                    )
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="business-category">Category</label>
                <input
                  id="business-category"
                  className="input"
                  value={form.category}
                  onChange={(event) => update("category", event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="business-city">City</label>
                <input
                  id="business-city"
                  className="input"
                  value={form.city}
                  onChange={(event) => update("city", event.target.value)}
                />
              </div>
            </div>
          )}
          {step === 1 && (
            <div className="grid grid-2">
              <div className="field">
                <label htmlFor="owner-name">Owner name</label>
                <input
                  id="owner-name"
                  className="input"
                  required
                  value={form.ownerName}
                  onChange={(event) => update("ownerName", event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="owner-phone">Phone in E.164 format</label>
                <input
                  id="owner-phone"
                  className="input"
                  type="tel"
                  required
                  placeholder="+234…"
                  value={form.ownerPhone}
                  onChange={(event) => update("ownerPhone", event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="owner-email">Email (optional)</label>
                <input
                  id="owner-email"
                  className="input"
                  type="email"
                  value={form.ownerEmail}
                  onChange={(event) => update("ownerEmail", event.target.value)}
                />
              </div>
            </div>
          )}
          {step === 2 && (
            <>
              <div className="field">
                <label htmlFor="phone-label">Identity label</label>
                <input
                  id="phone-label"
                  className="input"
                  value={form.phoneLabel}
                  onChange={(event) => update("phoneLabel", event.target.value)}
                />
              </div>
              <div className="alert alert-info">
                This creates an approved identity record. WhatsApp delivery and
                verification remain unavailable until the Meta integration is
                activated.
              </div>
            </>
          )}
          {step === 3 && (
            <div aria-busy={busy}>
              {bumpaConnection ? (
                <div className="alert alert-success" role="status">
                  Bumpa accepted the write-only credential. Connection status:{" "}
                  {titleCase(bumpaConnection.status)}.
                </div>
              ) : (
                <>
                  <div className="grid grid-2">
                    <div className="field">
                      <label htmlFor="bumpa-api-key">Bumpa API key</label>
                      <input
                        id="bumpa-api-key"
                        className="input"
                        type="password"
                        required
                        autoComplete="off"
                        autoCapitalize="none"
                        spellCheck={false}
                        disabled={busy}
                        aria-describedby="bumpa-key-help"
                        value={form.bumpaApiKey}
                        onChange={(event) =>
                          update("bumpaApiKey", event.target.value)
                        }
                      />
                      <span className="field-help" id="bumpa-key-help">
                        Write only. The key is encrypted by the API, never
                        returned, and cleared here after every attempt.
                      </span>
                    </div>
                    <div className="field">
                      <label htmlFor="bumpa-scope-type">Account scope</label>
                      <select
                        id="bumpa-scope-type"
                        className="select"
                        disabled={busy}
                        value={bumpaScopeType}
                        onChange={(event) =>
                          setBumpaScopeType(
                            event.target.value as "business_id" | "location_id",
                          )
                        }
                      >
                        <option value="business_id">Business</option>
                        <option value="location_id">Location</option>
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="bumpa-scope-id">
                        {bumpaScopeType === "business_id"
                          ? "Business ID"
                          : "Location ID"}
                      </label>
                      <input
                        id="bumpa-scope-id"
                        className="input"
                        required
                        autoComplete="off"
                        autoCapitalize="none"
                        spellCheck={false}
                        disabled={busy}
                        value={form.bumpaScopeId}
                        onChange={(event) =>
                          update("bumpaScopeId", event.target.value)
                        }
                      />
                    </div>
                  </div>
                  <div className="alert alert-info">
                    Saving asks the production API to verify this credential
                    directly with Bumpa before activating the connection.
                  </div>
                </>
              )}
            </div>
          )}
          {step === 4 && (
            <div aria-busy={busy}>
              {hermesProfile ? (
                <div className="alert alert-success" role="status">
                  Hermes profile {hermesProfile.profile_name} reports{" "}
                  {titleCase(hermesProfile.status)}.
                </div>
              ) : (
                <>
                  <h3>Provision an isolated agent profile</h3>
                  <p style={{ color: "var(--ink-soft)", lineHeight: 1.6 }}>
                    The API allocates this tenant its own authenticated Hermes
                    runtime profile. No model credential or profile key is
                    exposed to the browser.
                  </p>
                  <div className="alert alert-info">
                    Provisioning can report as in progress until the private
                    runtime health check succeeds. The exact state is retained
                    in the final review.
                  </div>
                </>
              )}
            </div>
          )}
          {step === 5 && (
            <div>
              <div className="alert alert-success">
                Onboarding records and provider setup requests are persisted.
                The states below are reported by the API.
              </div>
              {[
                ["Tenant", tenant?.name ?? "Not created"],
                ["Owner", form.ownerName],
                [
                  "WhatsApp identity",
                  phoneCreated ? "Recorded" : "Not recorded",
                ],
                [
                  "Bumpa",
                  bumpaConnection
                    ? `${titleCase(bumpaConnection.status)} · ${titleCase(bumpaConnection.provider)}`
                    : "Not connected",
                ],
                [
                  "Hermes",
                  hermesProfile
                    ? `${titleCase(hermesProfile.status)} · ${hermesProfile.profile_name}`
                    : "Not provisioned",
                ],
              ].map(([label, value]) => (
                <div className="detail-row" key={label}>
                  <span className="detail-value">{label}</span>
                  <Badge>{value}</Badge>
                </div>
              ))}
            </div>
          )}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 10,
              marginTop: 28,
            }}
          >
            <button
              className="button button-secondary"
              disabled={step === 0 || busy}
              onClick={() => setStep((current) => current - 1)}
            >
              ← Back
            </button>
            {step < steps.length - 1 ? (
              <button
                className="button button-primary"
                disabled={
                  busy ||
                  (step === 0 && (!form.name || !form.slug)) ||
                  (step === 1 && (!form.ownerName || !form.ownerPhone)) ||
                  (step === 2 && (!tenant || !owner)) ||
                  (step === 3 &&
                    (!tenant ||
                      (!bumpaConnection &&
                        (!form.bumpaApiKey || !form.bumpaScopeId)))) ||
                  (step === 4 && !tenant)
                }
                onClick={() => void continueStep()}
                aria-busy={busy}
              >
                {busy
                  ? "Saving…"
                  : step === 3
                    ? bumpaConnection
                      ? "Continue →"
                      : "Connect and verify Bumpa →"
                    : step === 4
                      ? hermesProfile
                        ? "Continue →"
                        : "Provision Hermes profile →"
                      : "Save and continue →"}
              </button>
            ) : tenant ? (
              <Link
                className="button button-primary"
                href={`/admin/tenants/${tenant.id}`}
              >
                Open tenant →
              </Link>
            ) : (
              <button className="button button-primary" disabled>
                Tenant not created
              </button>
            )}
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
