"use client";

import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import { Badge, Card, PageHeader, StatePanel, Toast } from "@/components/ui";
import { apiRequest, demoFallbackEnabled } from "@/lib/api";
import {
  durationBetween,
  formatDate,
  titleCase,
  type BumpaStatus,
  type SyncRun,
} from "@/lib/platform-data";
import { previewBumpaStatus, previewSyncRuns } from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";

export default function BumpaPage() {
  const connection = useApiResource<BumpaStatus>(
    "/settings/bumpa",
    previewBumpaStatus,
  );
  const runs = useApiResource<SyncRun[]>(
    "/bumpa/sync-runs",
    previewSyncRuns.filter((run) => run.tenant_id === "demo-kaia-home"),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const combinedStatus =
    connection.status === "error" || runs.status === "error"
      ? "error"
      : connection.status === "loading" || runs.status === "loading"
        ? "loading"
        : "ready";
  const combinedSource =
    connection.source === "demo" || runs.source === "demo"
      ? "demo"
      : connection.source === "live" && runs.source === "live"
        ? "live"
        : null;
  const refreshAvailable =
    connection.source === "live" &&
    connection.data?.status === "active" &&
    demoFallbackEnabled;
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      await apiRequest("/bumpa/sync/latest", { method: "POST" });
      await Promise.all([connection.reload(), runs.reload()]);
      setToast("The local Bumpa sync completed.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The refresh could not be completed.",
      );
    } finally {
      setBusy(false);
    }
  };
  const latest = runs.data?.[0];
  return (
    <AppShell surface="user" title="Bumpa connection">
      <PageHeader
        title="Bumpa data connection"
        description="Connection state and sync evidence returned by the workspace APIs."
        actions={
          <button
            className="button button-secondary"
            disabled={!refreshAvailable || busy}
            title={
              !refreshAvailable
                ? "Refresh activates with the live Bumpa adapter; local demo APIs can exercise the simulator."
                : undefined
            }
            onClick={() => void refresh()}
          >
            {busy ? "Refreshing…" : "↻ Request refresh"}
          </button>
        }
      />
      <LiveDataBanner
        label="Bumpa connection and sync history"
        source={combinedSource}
        status={combinedStatus}
        error={connection.error ?? runs.error}
      />
      {!demoFallbackEnabled && (
        <div className="alert alert-warning">
          Live Bumpa synchronisation has not been activated on this deployment.
          Refresh controls remain disabled until the adapter and credentials are
          configured.
        </div>
      )}
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {connection.status === "loading" ? (
        <StatePanel type="loading" />
      ) : connection.status === "error" || !connection.data ? (
        <StatePanel
          type="error"
          description={connection.error ?? undefined}
          action={
            <button
              className="button button-secondary"
              onClick={() => void connection.reload()}
            >
              Try again
            </button>
          }
        />
      ) : connection.data.status === "not_connected" ? (
        <StatePanel
          type="empty"
          title="Bumpa is not connected"
          description="A platform operator must add the encrypted business credentials before data can be synchronised."
          action={
            <button className="button button-secondary" disabled>
              Connect unavailable in workspace settings
            </button>
          }
        />
      ) : (
        <>
          <div className="grid grid-2">
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>Connection health</h2>
                  <p>Credential values remain write-only.</p>
                </div>
                <Badge>{titleCase(connection.data.status)}</Badge>
              </div>
              {[
                ["Provider", titleCase(connection.data.provider)],
                [
                  "Scope",
                  `${titleCase(connection.data.scope_type)} · •••• ${connection.data.scope_id_last4 ?? "unknown"}`,
                ],
                [
                  "Last successful sync",
                  formatDate(connection.data.last_successful_sync_at),
                ],
                ["Last error", connection.data.last_error ?? "None recorded"],
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
                  <h2>Latest run</h2>
                  <p>
                    Dataset availability is read from the latest recorded sync.
                  </p>
                </div>
                {latest && <Badge>{titleCase(latest.status)}</Badge>}
              </div>
              {latest ? (
                <>
                  {Object.entries(latest.dataset_results ?? {}).map(
                    ([dataset, result]) => (
                      <div className="detail-row" key={dataset}>
                        <span className="detail-value">
                          {titleCase(dataset)}
                        </span>
                        <Badge>
                          {typeof result === "string"
                            ? titleCase(result)
                            : "Recorded"}
                        </Badge>
                      </div>
                    ),
                  )}
                  {!Object.keys(latest.dataset_results ?? {}).length && (
                    <p className="table-secondary">
                      No dataset-level results were recorded.
                    </p>
                  )}
                </>
              ) : (
                <p className="table-secondary">
                  No sync run has been recorded.
                </p>
              )}
            </Card>
          </div>
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Recent sync activity</h2>
                <p>Newest runs returned by the workspace API.</p>
              </div>
            </div>
            {runs.status === "loading" ? (
              <StatePanel type="loading" />
            ) : runs.status === "error" ? (
              <p className="table-secondary">
                Sync history could not be loaded: {runs.error}
              </p>
            ) : runs.data?.length ? (
              <div className="timeline">
                {runs.data.map((run) => (
                  <div className="timeline-item" key={run.id}>
                    <strong>{titleCase(run.status)} sync</strong>
                    <p>
                      {run.requested_from && run.requested_to
                        ? `${run.requested_from} – ${run.requested_to}`
                        : "Date range not recorded"}{" "}
                      · {durationBetween(run.started_at, run.finished_at)}
                    </p>
                    <span className="tag">{formatDate(run.started_at)}</span>
                    {run.error && <p className="field-error">{run.error}</p>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="table-secondary">
                No sync runs have been recorded.
              </p>
            )}
          </Card>
        </>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
