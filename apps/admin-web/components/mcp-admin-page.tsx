"use client";

import { useMemo, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import {
  Badge,
  Card,
  Filters,
  Metric,
  Modal,
  PageHeader,
  StatePanel,
  Toast,
} from "@/components/ui";
import { apiRequest } from "@/lib/api";
import {
  formatDate,
  titleCase,
  type McpAdminConnection,
} from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";
import { usePersistentFilters } from "@/lib/use-persistent-filters";

type Decision = {
  connection: McpAdminConnection;
  decision: "approve" | "reject";
};

const CONNECTION_FILTERS = {
  q: { defaultValue: "" },
  status: {
    defaultValue: "all",
    allowedValues: [
      "all",
      "admin_pending",
      "approved",
      "oauth_in_progress",
      "active",
      "rejected",
    ],
  },
} as const;

export function McpApprovals() {
  const resource = useApiResource<McpAdminConnection[]>(
    "/admin/mcp-connections",
  );
  const { values: filters, setFilter } =
    usePersistentFilters(CONNECTION_FILTERS);
  const { q: search, status } = filters;
  const [pending, setPending] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (resource.data ?? []).filter(
      (connection) =>
        (status === "all" || connection.status === status) &&
        (!term ||
          `${connection.tenant_name} ${connection.provider} ${connection.status}`
            .toLowerCase()
            .includes(term)),
    );
  }, [resource.data, search, status]);
  const pendingCount = (resource.data ?? []).filter(
    (item) => item.status === "admin_pending",
  ).length;
  const activeCount = (resource.data ?? []).filter(
    (item) => item.status === "active",
  ).length;
  const writeCount = (resource.data ?? []).filter(
    (item) => !item.read_only,
  ).length;

  async function decide() {
    if (!pending || busy || reason.trim().length < 8) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/admin/mcp-connections/${pending.connection.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          decision: pending.decision,
          reason: reason.trim(),
        }),
      });
      await resource.reload();
      setToast(
        pending.decision === "approve"
          ? `${titleCase(pending.connection.provider)} approved for ${pending.connection.tenant_name}.`
          : `${titleCase(pending.connection.provider)} rejected and credentials cleared.`,
      );
      setPending(null);
      setReason("");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The approval decision could not be recorded.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Connection approvals">
      <PageHeader
        title="Connection approvals"
        description="Review tenant requests before any external OAuth credential can become active."
      />
      <LiveDataBanner
        label="MCP connection requests"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      <div className="alert alert-info">
        Providers and tools are fixed by the internal registry. Approval never
        reveals OAuth tokens. Write-capable requests remain denied tool by tool
        until the tenant enables confirmed-write permission.
      </div>
      {resource.status !== "ready" ? (
        <StatePanel
          type={resource.status === "loading" ? "loading" : "error"}
          description={resource.error ?? undefined}
          action={
            resource.status === "error" ? (
              <button
                type="button"
                className="button button-secondary"
                onClick={resource.reload}
              >
                Try again
              </button>
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="grid grid-3">
            <Metric
              label="Awaiting review"
              value={String(pendingCount)}
              note="Operator action"
            />
            <Metric
              label="Active"
              value={String(activeCount)}
              note="OAuth completed"
            />
            <Metric
              label="Write requested"
              value={String(writeCount)}
              note="Confirmation-gated"
            />
          </div>
          <Filters search={search} setSearch={(value) => setFilter("q", value)}>
            <label>
              <span className="sr-only">Filter by status</span>
              <select
                className="select"
                value={status}
                onChange={(event) => setFilter("status", event.target.value)}
              >
                <option value="all">All statuses</option>
                <option value="admin_pending">Awaiting review</option>
                <option value="approved">Approved</option>
                <option value="oauth_in_progress">Authorizing</option>
                <option value="active">Active</option>
                <option value="rejected">Rejected</option>
              </select>
            </label>
          </Filters>
          {!rows.length ? (
            <StatePanel
              type="empty"
              title="No matching connection requests"
              description="Requests appear here after a tenant administrator chooses an enabled registry connector."
            />
          ) : (
            <div className="grid">
              {rows.map((connection) => (
                <Card padded key={connection.id}>
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
                          flexWrap: "wrap",
                          marginBottom: 8,
                        }}
                      >
                        <strong>{connection.tenant_name}</strong>
                        <Badge>{titleCase(connection.status)}</Badge>
                        {!connection.read_only && (
                          <Badge>Controlled writes</Badge>
                        )}
                      </div>
                      <p className="table-secondary">
                        {titleCase(connection.provider)} · requested{" "}
                        {formatDate(connection.created_at)}
                      </p>
                      <p className="table-secondary">
                        {connection.scopes.length} fixed scope
                        {connection.scopes.length === 1 ? "" : "s"} ·{" "}
                        {Object.keys(connection.permissions).length} tool
                        policies
                      </p>
                    </div>
                    {connection.status === "admin_pending" && (
                      <div
                        style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
                      >
                        <button
                          type="button"
                          className="button button-primary button-small"
                          onClick={() => {
                            setReason("");
                            setError("");
                            setPending({ connection, decision: "approve" });
                          }}
                        >
                          Review approval
                        </button>
                        <button
                          type="button"
                          className="button button-secondary button-small"
                          onClick={() => {
                            setReason("");
                            setError("");
                            setPending({ connection, decision: "reject" });
                          }}
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
      {pending && (
        <Modal
          title={
            pending.decision === "approve"
              ? "Approve connection request"
              : "Reject connection request"
          }
          onClose={() => !busy && setPending(null)}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => setPending(null)}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                type="button"
                className={
                  pending.decision === "approve"
                    ? "button button-primary"
                    : "button button-danger"
                }
                onClick={() => void decide()}
                disabled={busy || reason.trim().length < 8}
                aria-busy={busy}
              >
                {busy
                  ? "Recording…"
                  : pending.decision === "approve"
                    ? "Approve request"
                    : "Reject and clear access"}
              </button>
            </>
          }
        >
          <p>
            <strong>{pending.connection.tenant_name}</strong> requested{" "}
            <strong>{titleCase(pending.connection.provider)}</strong> with{" "}
            {pending.connection.read_only ? "read-only" : "controlled-write"}{" "}
            access.
          </p>
          {!pending.connection.read_only && (
            <div className="alert alert-warning">
              Approval allows OAuth, but every write tool remains denied until
              the tenant explicitly enables confirmation-gated access.
            </div>
          )}
          <label className="field" htmlFor="approval-reason">
            <span>Decision reason</span>
            <textarea
              id="approval-reason"
              className="textarea"
              value={reason}
              maxLength={240}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Record the operational basis for this decision"
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
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
