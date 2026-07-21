"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AppIcon } from "@/components/app-icon";
import { apiRequest } from "@/lib/api";
import {
  type AdminExport,
  durationBetween,
  formatDate,
  maskPhone,
  titleCase,
  type AsyncJob,
  type AsyncJobReplayReason,
  type HermesCallError,
  type PlatformAccess,
  type SyncRun,
  type SystemError,
  type Tenant,
  type UsageEvent,
  type WhatsAppDeliveryFailure,
} from "@/lib/platform-data";

import { useApiResource } from "@/lib/use-api-resource";
import { usePersistentFilters } from "@/lib/use-persistent-filters";
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
  ScrollableTable,
  StatePanel,
  Toast,
} from "./ui";

const SEARCH_FILTERS = { q: { defaultValue: "" } } as const;
const TENANT_FILTERS = {
  q: { defaultValue: "" },
  status: {
    defaultValue: "all",
    allowedValues: ["all", "active", "suspended", "archived"],
  },
} as const;

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
            type="button"
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
  const tenantResource = useApiResource<Tenant[]>("/admin/tenants");
  const syncResource = useApiResource<SyncRun[]>("/admin/system/sync-runs");
  const errorResource = useApiResource<SystemError[]>("/admin/system/errors");
  const usageResource = useApiResource<UsageEvent[]>("/admin/usage");
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
  const overviewSource = overviewResources.every(
    (resource) => resource.source === "live",
  )
    ? "live"
    : null;
  const overviewError = overviewResources
    .flatMap((resource) => (resource.error ? [resource.error] : []))
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
    <AppShell title="Operations overview">
      <PageHeader
        title="Platform operations"
        description="Current tenant, sync, usage, and error evidence from the platform APIs."
        actions={
          <Link className="button button-primary" href="/onboarding">
            <AppIcon name="add" size={16} /> Onboard SME
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
              label="Recent system errors"
              value={
                errorResource.status === "ready" ? String(errors.length) : "—"
              }
              note="Bounded recent records returned by the API"
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
                <Link className="table-action" href="/failures">
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
              <Link className="table-action" href="/tenants">
                All tenants
              </Link>
            </div>
            {tenants.length ? (
              <ScrollableTable label="Tenant health">
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
                              href={`/tenants/${tenant.id}`}
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
              </ScrollableTable>
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
  const resource = useApiResource<Tenant[]>("/admin/tenants");
  const { values: filters, setFilter } = usePersistentFilters(TENANT_FILTERS);
  const { q: search, status } = filters;
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
    <AppShell title="Tenants">
      <PageHeader
        title="SME tenants"
        description="Onboard, monitor, and safely manage every isolated business workspace."
        actions={
          <Link className="button button-primary" href="/onboarding">
            <AppIcon name="add" size={16} /> Onboard SME
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
          <Filters search={search} setSearch={(value) => setFilter("q", value)}>
            <select
              className="filter-select"
              aria-label="Filter by status"
              value={status}
              onChange={(event) => setFilter("status", event.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
              <option value="archived">Archived</option>
            </select>
          </Filters>
          {rows.length ? (
            <ScrollableTable className="card" label="Tenant directory">
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
                          href={`/tenants/${tenant.id}`}
                        >
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollableTable>
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

export { TenantDetail } from "./tenant-detail";

type PendingPlatformAccess = {
  admin: PlatformAccess;
  role: "operator" | "researcher";
  action: "grant" | "revoke";
};

export function UserList() {
  const { values: filters, setFilter } = usePersistentFilters(SEARCH_FILTERS);
  const search = filters.q;
  const admins = useApiResource<PlatformAccess[]>("/admin/platform-access");
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
  const [mutationError, setMutationError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingAccess, setPendingAccess] =
    useState<PendingPlatformAccess | null>(null);
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

  const changeAccess = async () => {
    if (!pendingAccess) return;
    setBusy(true);
    setMutationError("");
    try {
      const { admin, role, action } = pendingAccess;
      await apiRequest<void>(
        `/admin/platform-access/${admin.user_id}/${role}`,
        { method: action === "grant" ? "PUT" : "DELETE" },
      );
      const displayName = admin.name?.trim() || "The collaborator";
      await admins.reload();
      setPendingAccess(null);
      const accessLabel = role === "operator" ? "admin" : "research";
      setToast(
        action === "grant"
          ? `${displayName} now has ${accessLabel} access.`
          : `${displayName}'s ${accessLabel} access was revoked.`,
      );
    } catch (reason) {
      setMutationError(
        reason instanceof Error
          ? reason.message
          : "Platform access could not be changed.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell title="Platform access">
      <PageHeader
        title="Platform access"
        description="Manage mapped collaborators and audit existing admin and research privileges."
        actions={
          <Link className="button button-secondary" href="/tenants">
            Manage tenant mappings
          </Link>
        }
      />
      <LiveDataBanner
        label="platform access directory"
        source={admins.source}
        status={admins.status}
        count={admins.data?.length}
        error={admins.error}
      />
      <div className="alert alert-info">
        <div>
          <strong>Platform access is separate from store access.</strong>
          <div>
            Admin access manages tenant operations. Research access opens
            consented, de-identified research tools. Store memberships are not
            changed here. To add someone, map their primary phone to an active
            workspace first, then return here to grant access.
          </div>
        </div>
      </div>
      {admins.status === "loading" ? (
        <StatePanel type="loading" />
      ) : admins.status === "error" ? (
        <StatePanel
          type="error"
          title="Platform access could not be loaded"
          description={admins.error ?? undefined}
          action={
            <button
              type="button"
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
          title="No mapped collaborators returned"
          description="Map a collaborator's primary phone before granting platform access."
          action={
            <Link className="button button-primary" href="/tenants">
              Map a collaborator
            </Link>
          }
        />
      ) : (
        <>
          <Filters
            search={search}
            setSearch={(value) => setFilter("q", value)}
          />
          {rows.length ? (
            <PlatformAccessTable
              busy={busy}
              currentUserId={session.data?.user.id}
              onChange={(value) => {
                setMutationError("");
                setPendingAccess(value);
              }}
              rows={rows}
              sessionReady={session.status === "ready"}
              source={admins.source}
            />
          ) : (
            <StatePanel
              type="empty"
              title="No matching collaborators"
              description="Clear or adjust your search to see other mapped collaborators."
              action={
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => setFilter("q", "")}
                >
                  Clear search
                </button>
              }
            />
          )}
        </>
      )}
      <PlatformAccessDialog
        busy={busy}
        error={mutationError}
        onClose={() => {
          setPendingAccess(null);
          setMutationError("");
        }}
        onConfirm={changeAccess}
        pending={pendingAccess}
      />
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

function PlatformAccessTable({
  busy,
  currentUserId,
  onChange,
  rows,
  sessionReady,
  source,
}: {
  busy: boolean;
  currentUserId?: string;
  onChange: (value: PendingPlatformAccess) => void;
  rows: PlatformAccess[];
  sessionReady: boolean;
  source: "live" | null;
}) {
  return (
    <ScrollableTable className="card" label="Platform access directory">
      <table className="data-table admin-directory-table">
        <thead>
          <tr>
            <th>Collaborator</th>
            <th>Phone</th>
            <th>Current access</th>
            <th>Status</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((admin) => {
            const isCurrent = currentUserId === admin.user_id;
            const isSuperadmin = admin.platform_roles.includes("superadmin");
            const displayName = admin.name?.trim() || "Unnamed admin";
            const initials = displayName
              .split(/\s+/)
              .flatMap((part) => (part[0] ? [part[0]] : []))
              .slice(0, 2)
              .join("")
              .toUpperCase();
            return (
              <tr key={admin.user_id}>
                <td>
                  <div className="admin-identity">
                    <span className="avatar" aria-hidden="true">
                      {initials}
                    </span>
                    <span>
                      <span className="table-primary">{displayName}</span>
                      <span className="table-secondary">
                        {isCurrent
                          ? "Your account"
                          : admin.has_active_mapping
                            ? "Mapped collaborator"
                            : "Mapping required"}
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
                    {admin.platform_roles.length ? (
                      admin.platform_roles.map((role) => (
                        <Badge key={role}>
                          {role === "operator" ? "Admin" : titleCase(role)}
                        </Badge>
                      ))
                    ) : (
                      <span className="table-secondary">Store only</span>
                    )}
                  </div>
                </td>
                <td>
                  <Badge>{titleCase(admin.status)}</Badge>
                </td>
                <td className="admin-directory-action">
                  <PlatformRoleActions
                    admin={admin}
                    busy={busy}
                    displayName={displayName}
                    isSuperadmin={isSuperadmin}
                    onChange={onChange}
                    sessionReady={sessionReady}
                    source={source}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ScrollableTable>
  );
}

function PlatformRoleActions({
  admin,
  busy,
  displayName,
  isSuperadmin,
  onChange,
  sessionReady,
  source,
}: {
  admin: PlatformAccess;
  busy: boolean;
  displayName: string;
  isSuperadmin: boolean;
  onChange: (value: PendingPlatformAccess) => void;
  sessionReady: boolean;
  source: "live" | null;
}) {
  if (!sessionReady)
    return <span className="admin-protected-label">Checking account…</span>;
  if (isSuperadmin)
    return <span className="admin-protected-label">Superadmin protected</span>;
  if (source !== "live")
    return <span className="admin-protected-label">API unavailable</span>;
  return (
    <div className="admin-role-list">
      {(["operator", "researcher"] as const).map((role) => {
        const granted = admin.platform_roles.includes(role);
        const label = role === "operator" ? "Admin" : "Research";
        return (
          <button
            type="button"
            key={role}
            className={`button button-small ${granted ? "button-danger" : "button-secondary"}`}
            disabled={
              busy ||
              (!granted &&
                (admin.status !== "active" || !admin.has_active_mapping))
            }
            title={
              !admin.has_active_mapping && !granted
                ? "Map this collaborator to an active workspace before granting access"
                : undefined
            }
            onClick={() =>
              onChange({ admin, role, action: granted ? "revoke" : "grant" })
            }
            aria-label={`${granted ? "Revoke" : "Grant"} ${displayName}'s ${label.toLowerCase()} access`}
          >
            {granted ? `Remove ${label}` : `Grant ${label}`}
          </button>
        );
      })}
    </div>
  );
}

function PlatformAccessDialog({
  busy,
  error,
  onClose,
  onConfirm,
  pending,
}: {
  busy: boolean;
  error: string;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  pending: PendingPlatformAccess | null;
}) {
  if (!pending) return null;
  const role = pending.role === "operator" ? "admin" : "research";
  return (
    <Modal
      title={`${pending.action === "grant" ? "Grant" : "Revoke"} ${role} access?`}
      onClose={() => !busy && onClose()}
      actions={
        <>
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            className={`button ${pending.action === "grant" ? "button-primary" : "button-danger"}`}
            disabled={busy}
            aria-busy={busy}
            onClick={() => void onConfirm()}
          >
            {busy
              ? "Saving…"
              : `${pending.action === "grant" ? "Grant" : "Revoke"} ${role} access`}
          </button>
        </>
      }
    >
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      <p className="modal-intro">
        <strong>{pending.admin.name?.trim() || "This collaborator"}</strong>
        {pending.action === "grant" ? " will gain " : " will lose "}
        {pending.role === "operator"
          ? "cross-tenant onboarding, mapping, and operations access"
          : "access to consented, de-identified research tools"}
        . Any store membership they hold remains unchanged.
      </p>
      <div className="alert alert-warning" style={{ marginBottom: 0 }}>
        This change takes effect immediately and is recorded in the audit trail.
      </div>
    </Modal>
  );
}

export function SyncList() {
  const runs = useApiResource<SyncRun[]>("/admin/system/sync-runs");
  const tenants = useApiResource<Tenant[]>("/admin/tenants");
  const names = new Map(
    (tenants.data ?? []).map((tenant) => [tenant.id, tenant.name]),
  );
  const successCount = (runs.data ?? []).filter(
    (run) => run.status === "success",
  ).length;
  const [selectedTenant, setSelectedTenant] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [toast, setToast] = useState("");

  async function queueSync() {
    if (!selectedTenant || syncing || tenants.source !== "live") return;
    const tenant = (tenants.data ?? []).find(
      (row) => row.id === selectedTenant,
    );
    if (
      !window.confirm(
        `Queue a 30-day Bumpa refresh for ${tenant?.name ?? "this tenant"}?`,
      )
    )
      return;
    const dateTo = new Date();
    const dateFrom = new Date(dateTo);
    dateFrom.setUTCDate(dateFrom.getUTCDate() - 29);
    setSyncing(true);
    setMutationError("");
    try {
      await apiRequest(`/admin/tenants/${selectedTenant}/bumpa/sync`, {
        method: "POST",
        headers: {
          "Idempotency-Key": `admin-${selectedTenant}-${Date.now()}`,
        },
        body: JSON.stringify({
          date_from: dateFrom.toISOString().slice(0, 10),
          date_to: dateTo.toISOString().slice(0, 10),
          reason: "operator_requested_refresh",
          confirmation: "trigger_bumpa_sync",
        }),
      });
      await runs.reload();
      setToast("Bumpa refresh queued and audit logged.");
    } catch (reason) {
      setMutationError(
        reason instanceof Error
          ? reason.message
          : "The Bumpa refresh could not be queued.",
      );
    } finally {
      setSyncing(false);
    }
  }
  return (
    <AppShell title="Sync runs">
      <PageHeader
        title="Bumpa sync runs"
        description="Monitor freshness and upstream failures from recorded sync runs."
        actions={
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <select
              className="select"
              aria-label="Tenant to refresh"
              value={selectedTenant}
              onChange={(event) => setSelectedTenant(event.target.value)}
              disabled={tenants.status !== "ready" || syncing}
            >
              <option value="">Choose tenant</option>
              {(tenants.data ?? []).map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button button-primary"
              disabled={!selectedTenant || tenants.source !== "live" || syncing}
              onClick={() => void queueSync()}
            >
              {syncing ? (
                "Queueing…"
              ) : (
                <>
                  <AppIcon name="refresh" size={16} /> Trigger sync
                </>
              )}
            </button>
          </div>
        }
      />
      {mutationError && (
        <div className="alert alert-danger" role="alert">
          {mutationError}
        </div>
      )}
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
          <ScrollableTable
            className="card"
            label="Bumpa sync runs"
            style={{ marginTop: 18 }}
          >
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
          </ScrollableTable>
        </>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

export function ErrorList() {
  const errorResource = useApiResource<SystemError[]>("/admin/system/errors");
  const jobResource = useApiResource<AsyncJob[]>(
    "/admin/system/jobs?status=dead_letter",
  );
  const { values: filters, setFilter } = usePersistentFilters(SEARCH_FILTERS);
  const search = filters.q;
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
    <AppShell title="Failure recovery">
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
                    type="button"
                    className="button button-primary button-small"
                    onClick={() => {
                      setReplayError(null);
                      setReplayReason("operator_verified_safe_retry");
                      setSelectedJob(job);
                    }}
                    disabled={!job.replayable || jobResource.source !== "live"}
                  >
                    Review replay
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
            <Filters
              search={search}
              setSearch={(value) => setFilter("q", value)}
            />
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
                type="button"
                className="button button-secondary"
                onClick={() => setSelectedJob(null)}
                disabled={replayPending}
              >
                Cancel
              </button>
              <button
                type="button"
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

export function ProviderFailures() {
  const whatsapp = useApiResource<WhatsAppDeliveryFailure[]>(
    "/admin/system/whatsapp-delivery-failures",
  );
  const hermes = useApiResource<HermesCallError[]>(
    "/admin/system/hermes-call-errors",
  );
  const { values: filters, setFilter } = usePersistentFilters(SEARCH_FILTERS);
  const search = filters.q;
  const whatsappRows = (whatsapp.data ?? []).filter((row) =>
    `${row.status} ${row.provider_error_code ?? ""} ${row.phone_masked ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const hermesRows = (hermes.data ?? []).filter((row) =>
    `${row.category} ${row.profile_reference ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );

  return (
    <AppShell title="Provider failures">
      <PageHeader
        title="Provider failures"
        description="Bounded WhatsApp delivery and Hermes runtime diagnostics without message content, credentials, raw provider IDs, prompts, or stack traces."
      />
      <div className="grid grid-2">
        <Metric
          label="WhatsApp delivery failures"
          value={
            whatsapp.status === "ready"
              ? String(whatsapp.data?.length ?? 0)
              : "—"
          }
          note="Failed, rejected, or undeliverable statuses"
        />
        <Metric
          label="Hermes call errors"
          value={
            hermes.status === "ready" ? String(hermes.data?.length ?? 0) : "—"
          }
          note="Safe category and retryability only"
        />
      </div>
      <Filters search={search} setSearch={(value) => setFilter("q", value)} />
      <section aria-labelledby="whatsapp-failures-heading">
        <div className="section-title">
          <div>
            <h2 id="whatsapp-failures-heading">WhatsApp delivery failures</h2>
            <p>
              Use the hashed reference to correlate with protected provider
              tooling.
            </p>
          </div>
        </div>
        {whatsapp.status !== "ready" ? (
          <ResourceState
            status={whatsapp.status}
            error={whatsapp.error}
            onRetry={whatsapp.reload}
          />
        ) : whatsappRows.length ? (
          <ScrollableTable className="card" label="WhatsApp delivery failures">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Recorded</th>
                  <th>Status</th>
                  <th>Recipient</th>
                  <th>Provider code</th>
                  <th>Reference</th>
                </tr>
              </thead>
              <tbody>
                {whatsappRows.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDate(row.created_at)}</td>
                    <td>
                      <Badge tone="danger">{titleCase(row.status)}</Badge>
                    </td>
                    <td>{row.phone_masked ?? "Not linked"}</td>
                    <td>{row.provider_error_code ?? "Not supplied"}</td>
                    <td>
                      <code>{row.message_reference}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollableTable>
        ) : (
          <StatePanel
            type="empty"
            title="No WhatsApp delivery failures"
            description="No matching failed delivery events are in the current operations window."
          />
        )}
      </section>
      <section
        style={{ marginTop: 28 }}
        aria-labelledby="hermes-errors-heading"
      >
        <div className="section-title">
          <div>
            <h2 id="hermes-errors-heading">Hermes call errors</h2>
            <p>
              Categories are allowlisted; upstream response text never reaches
              this view.
            </p>
          </div>
        </div>
        {hermes.status !== "ready" ? (
          <ResourceState
            status={hermes.status}
            error={hermes.error}
            onRetry={hermes.reload}
          />
        ) : hermesRows.length ? (
          <ScrollableTable className="card" label="Hermes call errors">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Recorded</th>
                  <th>Category</th>
                  <th>Retryable</th>
                  <th>Profile reference</th>
                  <th>Tenant</th>
                </tr>
              </thead>
              <tbody>
                {hermesRows.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDate(row.created_at)}</td>
                    <td>
                      <Badge>{titleCase(row.category)}</Badge>
                    </td>
                    <td>
                      {row.retryable === null
                        ? "Unknown"
                        : row.retryable
                          ? "Yes"
                          : "No"}
                    </td>
                    <td>
                      <code>{row.profile_reference ?? "Not linked"}</code>
                    </td>
                    <td>{row.tenant_id?.slice(0, 8) ?? "Platform"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollableTable>
        ) : (
          <StatePanel
            type="empty"
            title="No Hermes call errors"
            description="No matching safe Hermes diagnostics are in the current operations window."
          />
        )}
      </section>
    </AppShell>
  );
}

function downloadText(filename: string, contentType: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: contentType }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function UsageList() {
  const usage = useApiResource<UsageEvent[]>("/admin/usage");
  const tenants = useApiResource<Tenant[]>("/admin/tenants");
  const names = new Map(
    (tenants.data ?? []).map((tenant) => [tenant.id, tenant.name]),
  );
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [toast, setToast] = useState("");

  async function generateExport() {
    if (exporting || usage.source !== "live") return;
    if (
      !window.confirm(
        "Generate a server-side tenant operations export? The export digest and operator are audit logged.",
      )
    )
      return;
    setExporting(true);
    setExportError("");
    try {
      const exported = await apiRequest<AdminExport>("/admin/exports", {
        method: "POST",
        body: JSON.stringify({
          scope: "tenant_operations",
          format: "csv",
          confirmation: "generate_admin_export",
        }),
      });
      downloadText(exported.filename, exported.content_type, exported.content);
      setToast(`${exported.row_count} tenant rows exported and audit logged.`);
    } catch (reason) {
      setExportError(
        reason instanceof Error
          ? reason.message
          : "The admin export could not be generated.",
      );
    } finally {
      setExporting(false);
    }
  }
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
    <AppShell title="Usage">
      <PageHeader
        title="Usage and capacity"
        description="Recorded usage events grouped without inventing cost or active-user metrics."
        actions={
          <button
            type="button"
            className="button button-secondary"
            disabled={usage.source !== "live" || exporting}
            onClick={() => void generateExport()}
          >
            {exporting ? (
              "Generating…"
            ) : (
              <>
                <AppIcon name="download" size={16} /> Generate audited export
              </>
            )}
          </button>
        }
      />
      {exportError && (
        <div className="alert alert-danger" role="alert">
          {exportError}
        </div>
      )}
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
          <ScrollableTable
            className="card"
            label="Usage by tenant"
            style={{ marginTop: 18 }}
          >
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
          </ScrollableTable>
          <div className="alert alert-info">
            The current API records event names and timestamps. Token cost and
            active-user metrics are intentionally omitted because the API does
            not provide them.
          </div>
        </>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
