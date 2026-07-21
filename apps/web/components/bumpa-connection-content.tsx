import { AppIcon } from "@/components/app-icon";
import { Badge, Card, StatePanel } from "@/components/ui";
import {
  durationBetween,
  formatDate,
  titleCase,
  type BumpaStatus,
  type SyncRun,
} from "@/lib/platform-data";

export function BumpaConnectionContent({
  acceptedLimitationMessage,
  connection,
  connectionError,
  connectionStatus,
  degradedPartial,
  latest,
  onReloadConnection,
  runs,
  runsError,
  runsStatus,
  unverifiedLegacyRun,
  usableAcceptedPartial,
}: {
  acceptedLimitationMessage: string;
  connection: BumpaStatus | null;
  connectionError: string | null;
  connectionStatus: "loading" | "ready" | "error";
  degradedPartial: boolean;
  latest: SyncRun | undefined;
  onReloadConnection: () => Promise<void>;
  runs: SyncRun[] | null;
  runsError: string | null;
  runsStatus: "loading" | "ready" | "error";
  unverifiedLegacyRun: boolean;
  usableAcceptedPartial: boolean;
}) {
  if (connectionStatus === "loading") return <StatePanel type="loading" />;
  if (connectionStatus === "error" || !connection) {
    return (
      <StatePanel
        type="error"
        description={connectionError ?? undefined}
        action={
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void onReloadConnection()}
          >
            Try again
          </button>
        }
      />
    );
  }
  if (connection.status === "not_connected") {
    return (
      <StatePanel
        type="empty"
        title="Bumpa is not connected"
        description="Ask the workspace owner to connect the encrypted business credentials before data can be synchronised."
        action={
          <button type="button" className="button button-secondary" disabled>
            Connect unavailable in workspace settings
          </button>
        }
      />
    );
  }

  return (
    <>
      <div className="grid grid-2">
        <ConnectionHealth connection={connection} />
        <LatestRun
          acceptedLimitationMessage={acceptedLimitationMessage}
          degradedPartial={degradedPartial}
          latest={latest}
          unverifiedLegacyRun={unverifiedLegacyRun}
          usableAcceptedPartial={usableAcceptedPartial}
        />
      </div>
      <Card padded>
        <div className="card-head">
          <div>
            <h2>Recent sync activity</h2>
            <p>Newest runs returned by the workspace API.</p>
          </div>
        </div>
        {runsStatus === "loading" ? (
          <StatePanel type="loading" />
        ) : runsStatus === "error" ? (
          <p className="table-secondary">
            Sync history could not be loaded: {runsError}
          </p>
        ) : runs?.length ? (
          <div className="timeline">
            {runs.map((run) => (
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
          <p className="table-secondary">No sync runs have been recorded.</p>
        )}
      </Card>
    </>
  );
}

function ConnectionHealth({ connection }: { connection: BumpaStatus }) {
  const details = [
    ["Provider", titleCase(connection.provider)],
    [
      "Scope",
      `${titleCase(connection.scope_type)} · •••• ${connection.scope_id_last4 ?? "unknown"}`,
    ],
    ["Last data refresh", formatDate(connection.last_successful_sync_at)],
    ...(connection.last_error ? [["Last error", connection.last_error]] : []),
  ];
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>Connection health</h2>
          <p>Credential values remain write-only.</p>
        </div>
        <Badge>{titleCase(connection.status)}</Badge>
      </div>
      {details.map(([label, value]) => (
        <div className="detail-row" key={label}>
          <span className="detail-label">{label}</span>
          <span className="detail-value">{value}</span>
        </div>
      ))}
    </Card>
  );
}

function LatestRun({
  acceptedLimitationMessage,
  degradedPartial,
  latest,
  unverifiedLegacyRun,
  usableAcceptedPartial,
}: {
  acceptedLimitationMessage: string;
  degradedPartial: boolean;
  latest: SyncRun | undefined;
  unverifiedLegacyRun: boolean;
  usableAcceptedPartial: boolean;
}) {
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>Latest run</h2>
          <p>Dataset availability is read from the latest recorded sync.</p>
        </div>
        {latest && <Badge>{titleCase(latest.status)}</Badge>}
      </div>
      {latest ? (
        <>
          {usableAcceptedPartial && (
            <SyncNotice
              label="Bumpa data limitation"
              message={acceptedLimitationMessage}
              title="Most data is current"
              tone="info"
            />
          )}
          {degradedPartial && (
            <SyncNotice
              label="Bumpa data warning"
              message="This refresh could not update every required dataset. Review dataset availability before relying on these figures."
              title="Some data needs attention"
              tone="warning"
            />
          )}
          {unverifiedLegacyRun && (
            <SyncNotice
              label="Bumpa data verification warning"
              message="This run was completed by an earlier app version without the evidence required to mark its data as current. Request a new refresh before relying on it."
              title="Latest refresh is unverified"
              tone="warning"
            />
          )}
          {Object.entries(latest.dataset_results ?? {}).map(
            ([dataset, result]) => (
              <div className="detail-row" key={dataset}>
                <span className="detail-value">{titleCase(dataset)}</span>
                <Badge>
                  {typeof result === "string" ? titleCase(result) : "Recorded"}
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
        <p className="table-secondary">No sync run has been recorded.</p>
      )}
    </Card>
  );
}

function SyncNotice({
  label,
  message,
  title,
  tone,
}: {
  label: string;
  message: string;
  title: string;
  tone: "info" | "warning";
}) {
  return (
    <div
      className={`alert alert-${tone} sync-quality-notice`}
      role="status"
      aria-label={label}
    >
      <AppIcon name={tone === "info" ? "help" : "alert"} />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}
