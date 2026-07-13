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
type SyncJobStatus =
  | "pending"
  | "queued"
  | "running"
  | "retry"
  | "succeeded"
  | "dead_letter"
  | "cancelled";
type SyncJobCorrelation = {
  job_id: string;
  status: SyncJobStatus;
  sync_run_id?: string | null;
};
const terminalSyncStatuses = new Set<TerminalSyncStatus>([
  "success",
  "partial",
  "failed",
]);
const activeSyncJobStatuses = new Set<SyncJobStatus>([
  "pending",
  "queued",
  "running",
  "retry",
]);
const syncPollDelaysMs = [1000, 2000, 4000, 8000, 12000, 15000, 18000];

function isTerminalSyncStatus(status: string): status is TerminalSyncStatus {
  return terminalSyncStatuses.has(status as TerminalSyncStatus);
}

function isUsableProfitPartial(
  run:
    | Pick<SyncRun, "completion_quality" | "partial_reason" | "status">
    | undefined,
): boolean {
  return (
    run?.status === "partial" &&
    run.completion_quality === "accepted_partial" &&
    run.partial_reason === "profit_not_calculable"
  );
}

function unavailableProfitLabel(
  run: Pick<SyncRun, "dataset_results"> | undefined,
): string {
  const unavailableDatasets = Object.entries(run?.dataset_results ?? {})
    .filter(([, availability]) => availability === "unavailable")
    .map(([dataset]) => dataset);
  const grossUnavailable = unavailableDatasets.some(
    (dataset) =>
      dataset === "gross_profit" || dataset.endsWith(".gross_profit"),
  );
  const netUnavailable = unavailableDatasets.some(
    (dataset) => dataset === "net_profit" || dataset.endsWith(".net_profit"),
  );
  if (grossUnavailable && netUnavailable) return "Gross and net profit";
  if (grossUnavailable) return "Gross profit";
  if (netUnavailable) return "Net profit";
  return "Profit metrics";
}

function profitLimitationMessage(
  run: Pick<SyncRun, "dataset_results"> | undefined,
): string {
  const label = unavailableProfitLabel(run);
  const verb =
    label === "Gross and net profit" || label === "Profit metrics"
      ? "are"
      : "is";
  return `${label} ${verb} unavailable because Bumpa cannot calculate ${verb === "are" ? "them" : "it"} for this store. All other data from this refresh is current.`;
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
  const [toastTone, setToastTone] = useState<"success" | "warning">("success");
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
    jobId: string,
    signal: AbortSignal,
  ): Promise<TerminalSyncRun> => {
    let lastPollError: Error | null = null;
    for (const delayMs of syncPollDelaysMs) {
      await waitForPoll(delayMs, signal);
      let job: SyncJobCorrelation;
      try {
        job = await apiRequest<SyncJobCorrelation>(
          `/bumpa/sync-jobs/${encodeURIComponent(jobId)}`,
          { signal },
        );
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
      if (job.job_id !== jobId) {
        throw new Error(
          "The refresh status response did not match the requested job.",
        );
      }
      if (job.status === "dead_letter" || job.status === "cancelled") {
        throw new Error(
          `The Bumpa refresh job was ${job.status === "dead_letter" ? "stopped after repeated failures" : "cancelled"}. Please try again or contact support.`,
        );
      }
      if (activeSyncJobStatuses.has(job.status)) continue;
      if (job.status !== "succeeded") {
        throw new Error("The Bumpa refresh returned an unknown job status.");
      }
      if (!job.sync_run_id) {
        throw new Error(
          "The Bumpa refresh completed without a correlated sync record.",
        );
      }
      let nextRuns: SyncRun[];
      try {
        nextRuns = await apiRequest<SyncRun[]>("/bumpa/sync-runs", { signal });
      } catch (reason) {
        if (reason instanceof Error && reason.name === "AbortError")
          throw reason;
        lastPollError =
          reason instanceof Error
            ? reason
            : new Error("The sync history request failed.");
        continue;
      }
      runs.replace(nextRuns);
      const requestedRun = nextRuns.find((run) => run.id === job.sync_run_id);
      if (!requestedRun) {
        throw new Error(
          "The Bumpa refresh completed, but its exact sync record was not returned.",
        );
      }
      if (!isTerminalSyncStatus(requestedRun.status)) {
        throw new Error(
          "The Bumpa refresh completed, but its correlated sync record is not terminal.",
        );
      }
      return { ...requestedRun, status: requestedRun.status };
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
    setToastTone("success");
    try {
      const accepted = await apiRequest<{
        status: "queued" | "success" | "partial" | "failed";
        job_id?: string;
        error?: string | null;
        completion_quality?: SyncRun["completion_quality"];
        partial_reason?: SyncRun["partial_reason"];
        dataset_results?: SyncRun["dataset_results"];
      }>("/bumpa/sync/latest", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        signal: controller.signal,
      });
      let finalResult = accepted;
      if (accepted.status === "queued") {
        if (!accepted.job_id) {
          throw new Error(
            "The Bumpa refresh was queued without a trackable job identifier.",
          );
        }
        const completedRun = await pollForTerminalRun(
          accepted.job_id,
          controller.signal,
        );
        finalResult = completedRun;
      } else {
        await runs.reload();
      }
      await connection.reload();
      if (finalResult.status === "failed") {
        setError(
          finalResult.error || "Bumpa refresh failed. Please try again.",
        );
      } else if (finalResult.status === "partial") {
        if (
          finalResult.completion_quality === "accepted_partial" &&
          finalResult.partial_reason === "profit_not_calculable"
        ) {
          setToast(
            `Bumpa data refreshed. ${profitLimitationMessage(finalResult)}`,
          );
        } else {
          setToastTone("warning");
          setToast(
            "Bumpa refresh completed, but some data could not be refreshed. Review the latest run for details.",
          );
        }
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
  const usableProfitPartial = isUsableProfitPartial(latest);
  const unavailableProfitMessage = usableProfitPartial
    ? profitLimitationMessage(latest)
    : "";
  const degradedPartial =
    latest?.status === "partial" && latest.completion_quality === "degraded";
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
                  "Last data refresh",
                  formatDate(connection.data.last_successful_sync_at),
                ],
                ...(connection.data.last_error
                  ? [["Last error", connection.data.last_error]]
                  : []),
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
                  {usableProfitPartial && (
                    <div
                      className="alert alert-info sync-quality-notice"
                      role="status"
                      aria-label="Bumpa data limitation"
                    >
                      <span aria-hidden="true">ⓘ</span>
                      <div>
                        <strong>Most data is current</strong>
                        <p>{unavailableProfitMessage}</p>
                      </div>
                    </div>
                  )}
                  {degradedPartial && (
                    <div
                      className="alert alert-warning sync-quality-notice"
                      role="status"
                      aria-label="Bumpa data warning"
                    >
                      <span aria-hidden="true">!</span>
                      <div>
                        <strong>Some data needs attention</strong>
                        <p>
                          This refresh could not update every required dataset.
                          Review dataset availability before relying on these
                          figures.
                        </p>
                      </div>
                    </div>
                  )}
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
      {toast && (
        <Toast message={toast} tone={toastTone} onClose={() => setToast("")} />
      )}
    </AppShell>
  );
}
