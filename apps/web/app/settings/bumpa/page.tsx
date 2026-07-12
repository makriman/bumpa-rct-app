"use client";

import { useEffect, useRef, useState } from "react";
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

const previewTenantRuns = previewSyncRuns.filter(
  (run) => run.tenant_id === "demo-kaia-home",
);
type TerminalSyncStatus = "success" | "partial" | "failed";
type TerminalSyncRun = Omit<SyncRun, "status"> & {
  status: TerminalSyncStatus;
};
const terminalSyncStatuses = new Set<TerminalSyncStatus>([
  "success",
  "partial",
  "failed",
]);
const syncPollDelaysMs = [1000, 2000, 4000, 8000, 12000, 15000, 18000];

function isTerminalSyncStatus(status: string): status is TerminalSyncStatus {
  return terminalSyncStatuses.has(status as TerminalSyncStatus);
}

function abortError(): Error {
  const error = new Error("Polling was cancelled");
  error.name = "AbortError";
  return error;
}

function waitForPoll(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const cancel = () => {
      window.clearTimeout(timer);
      reject(abortError());
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", cancel, { once: true });
  });
}

export default function BumpaPage() {
  const connection = useApiResource<BumpaStatus>(
    "/settings/bumpa",
    previewBumpaStatus,
  );
  const runs = useApiResource<SyncRun[]>("/bumpa/sync-runs", previewTenantRuns);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const activePoll = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      activePoll.current?.abort();
    },
    [],
  );
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
  const localSimulator =
    demoFallbackEnabled && connection.data?.provider === "local";
  const refreshAvailable =
    connection.source === "live" &&
    connection.data?.status === "active" &&
    runs.status === "ready" &&
    (connection.data.provider === "bumpa" || localSimulator);

  const pollForTerminalRun = async (
    knownRunIds: Set<string>,
    signal: AbortSignal,
  ): Promise<TerminalSyncRun> => {
    let lastPollError: Error | null = null;
    for (const delayMs of syncPollDelaysMs) {
      await waitForPoll(delayMs, signal);
      let nextRuns: SyncRun[];
      try {
        nextRuns = await apiRequest<SyncRun[]>("/bumpa/sync-runs", { signal });
        lastPollError = null;
      } catch (reason) {
        if (reason instanceof Error && reason.name === "AbortError")
          throw reason;
        lastPollError =
          reason instanceof Error
            ? reason
            : new Error("The sync status request failed.");
        continue;
      }
      runs.replace(nextRuns);
      const requestedRun = nextRuns.find((run) => !knownRunIds.has(run.id));
      if (requestedRun && isTerminalSyncStatus(requestedRun.status)) {
        return { ...requestedRun, status: requestedRun.status };
      }
    }
    if (lastPollError) {
      throw new Error(
        `The refresh may still be processing, but its status could not be confirmed: ${lastPollError.message}`,
      );
    }
    throw new Error(
      "The refresh is still processing after one minute. Check sync activity before requesting another refresh.",
    );
  };

  const refresh = async () => {
    const controller = new AbortController();
    activePoll.current?.abort();
    activePoll.current = controller;
    setBusy(true);
    setError("");
    setToast("");
    try {
      const accepted = await apiRequest<{
        status: "queued" | "success" | "partial" | "failed";
        job_id?: string;
        error?: string | null;
      }>("/bumpa/sync/latest", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        signal: controller.signal,
      });
      let finalStatus = accepted.status;
      let finalError = accepted.error;
      if (accepted.status === "queued") {
        const completedRun = await pollForTerminalRun(
          new Set((runs.data ?? []).map((run) => run.id)),
          controller.signal,
        );
        finalStatus = completedRun.status;
        finalError = completedRun.error;
      } else {
        await runs.reload();
      }
      await connection.reload();
      if (finalStatus === "failed") {
        setError(finalError || "Bumpa refresh failed. Please try again.");
      } else if (finalStatus === "partial") {
        setToast(
          "Bumpa refresh completed with unavailable datasets. Review the latest run for details.",
        );
      } else {
        setToast("Bumpa refresh completed successfully.");
      }
    } catch (reason) {
      if (reason instanceof Error && reason.name === "AbortError") return;
      setError(
        reason instanceof Error
          ? reason.message
          : "The refresh could not be completed.",
      );
    } finally {
      if (activePoll.current === controller) {
        activePoll.current = null;
        setBusy(false);
      }
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
                ? "Refresh requires an active live Bumpa connection."
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
      {connection.source === "live" &&
        connection.data &&
        connection.data.provider !== "bumpa" &&
        !localSimulator && (
          <div className="alert alert-warning">
            Live Bumpa synchronisation has not been activated on this
            deployment. Refresh remains disabled until the provider is
            configured.
          </div>
        )}
      {connection.source === "live" && localSimulator && (
        <div className="alert alert-info">
          Local simulator active. Refreshes exercise the full API, database, and
          sync workflow without contacting a live Bumpa account.
        </div>
      )}
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      <span className="sr-only" role="status" aria-live="polite">
        {busy ? "Bumpa refresh is in progress." : ""}
      </span>
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
