"use client";

import { useEffect, useRef, useState } from "react";
import { AppIcon } from "@/components/app-icon";
import { AppShell } from "@/components/app-shell";
import { BumpaConnectionContent } from "@/components/bumpa-connection-content";
import { LiveDataBanner } from "@/components/live-data-banner";
import { PageHeader, Toast } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { type BumpaStatus, type SyncRun } from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";
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

function isUsableAcceptedPartial(
  run:
    | Pick<SyncRun, "completion_quality" | "partial_reason" | "status">
    | undefined,
): boolean {
  return (
    run?.status === "partial" &&
    run.completion_quality === "accepted_partial" &&
    (run.partial_reason === "profit_not_calculable" ||
      run.partial_reason === "optional_dataset_unavailable")
  );
}

function unavailableProfitLabel(
  run: Pick<SyncRun, "dataset_results"> | undefined,
): string {
  let grossUnavailable = false;
  let netUnavailable = false;
  for (const [dataset, availability] of Object.entries(
    run?.dataset_results ?? {},
  )) {
    if (availability !== "unavailable") continue;
    if (dataset === "gross_profit" || dataset.endsWith(".gross_profit"))
      grossUnavailable = true;
    if (dataset === "net_profit" || dataset.endsWith(".net_profit"))
      netUnavailable = true;
  }
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

function acceptedPartialMessage(
  run: Pick<SyncRun, "dataset_results" | "partial_reason">,
): string {
  if (run.partial_reason === "optional_dataset_unavailable") {
    return "The inventory overview is temporarily unavailable from Bumpa. All other data from this refresh is current.";
  }
  return profitLimitationMessage(run);
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
  const connection = useApiResource<BumpaStatus>("/settings/bumpa");
  const runs = useApiResource<SyncRun[]>("/bumpa/sync-runs");
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
    connection.source === "live" && runs.source === "live" ? "live" : null;
  const refreshAvailable =
    connection.source === "live" &&
    connection.data?.status === "active" &&
    runs.status === "ready" &&
    connection.data.provider === "bumpa";

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
          (finalResult.partial_reason === "profit_not_calculable" ||
            finalResult.partial_reason === "optional_dataset_unavailable")
        ) {
          setToast(
            `Bumpa data refreshed. ${acceptedPartialMessage(finalResult)}`,
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
      }
      setBusy(false);
    }
  };
  const latest = runs.data?.[0];
  const usableAcceptedPartial = isUsableAcceptedPartial(latest);
  const acceptedLimitationMessage =
    latest && usableAcceptedPartial ? acceptedPartialMessage(latest) : "";
  const degradedPartial =
    latest?.status === "partial" && latest.completion_quality === "degraded";
  const unverifiedLegacyRun =
    latest !== undefined &&
    isTerminalSyncStatus(latest.status) &&
    latest.completion_quality === "legacy";
  return (
    <AppShell title="Bumpa connection">
      <PageHeader
        title="Bumpa data connection"
        description="Connection state and sync evidence returned by the workspace APIs."
        actions={
          <button
            type="button"
            className="button button-secondary"
            disabled={!refreshAvailable || busy}
            title={
              !refreshAvailable
                ? "Refresh requires an active live Bumpa connection."
                : undefined
            }
            onClick={() => void refresh()}
          >
            {busy ? (
              "Refreshing…"
            ) : (
              <>
                <AppIcon name="refresh" size={16} /> Request refresh
              </>
            )}
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
        connection.data.provider !== "bumpa" && (
          <div className="alert alert-warning">
            Live Bumpa synchronisation has not been activated on this
            deployment. Refresh remains disabled until the provider is
            configured.
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
      <BumpaConnectionContent
        acceptedLimitationMessage={acceptedLimitationMessage}
        connection={connection.data}
        connectionError={connection.error}
        connectionStatus={connection.status}
        degradedPartial={degradedPartial}
        latest={latest}
        onReloadConnection={connection.reload}
        runs={runs.data}
        runsError={runs.error}
        runsStatus={runs.status}
        unverifiedLegacyRun={unverifiedLegacyRun}
        usableAcceptedPartial={usableAcceptedPartial}
      />
      {toast && (
        <Toast message={toast} tone={toastTone} onClose={() => setToast("")} />
      )}
    </AppShell>
  );
}
