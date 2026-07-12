"use client";

import { useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";
import {
  countValues,
  formatDate,
  titleCase,
  type Report,
  type ResearchConversationDetail,
  type ResearchConversationSummary,
  type ResearchEvent,
  type ResearchOverviewData,
  type Taxonomy,
} from "@/lib/platform-data";
import {
  previewReports,
  previewResearchConversationDetails,
  previewResearchConversations,
  previewResearchEvents,
  previewResearchOverview,
  previewTaxonomy,
} from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import {
  Badge,
  Card,
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
  retry,
  empty,
}: {
  status: "loading" | "ready" | "error";
  error: string | null;
  retry: () => Promise<void>;
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
            onClick={() => void retry()}
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
        description="The API returned no consented research records for this view."
      />
    );
  return null;
}

function Distribution({
  title,
  description,
  values,
}: {
  title: string;
  description: string;
  values: Array<[string, number]>;
}) {
  const total = values.reduce((sum, [, count]) => sum + count, 0);
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      {values.length ? (
        values.slice(0, 8).map(([label, count]) => {
          const ratio = total ? Math.round((count / total) * 100) : 0;
          return (
            <div key={label} style={{ marginBottom: 15 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 12,
                  fontSize: 13,
                  marginBottom: 6,
                }}
              >
                <span>{titleCase(label)}</span>
                <strong>
                  {count} · {ratio}%
                </strong>
              </div>
              <div className="progress">
                <span style={{ width: `${ratio}%` }} />
              </div>
            </div>
          );
        })
      ) : (
        <p className="table-secondary">No classified events were returned.</p>
      )}
    </Card>
  );
}

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ResearchOverview() {
  const overview = useApiResource<ResearchOverviewData>(
    "/research/overview",
    previewResearchOverview,
  );
  const events = useApiResource<ResearchEvent[]>(
    "/research/events",
    previewResearchEvents,
  );
  const data = overview.data;
  return (
    <AppShell surface="research" title="Research overview">
      <PageHeader
        title="Research overview"
        description="Pseudonymised evidence returned by the consent-filtered research APIs."
        actions={
          <button
            className="button button-secondary"
            disabled={!data}
            onClick={() => data && downloadJson("research-overview.json", data)}
          >
            ⇩ Export loaded view
          </button>
        }
      />
      <LiveDataBanner
        label="research overview"
        source={overview.source}
        status={overview.status}
        error={overview.error}
      />
      <div className="alert alert-info">
        This surface uses redacted research records. Raw identities and raw
        message text are not requested by the frontend.
      </div>
      {overview.status !== "ready" || !data ? (
        <ResourceState
          status={overview.status}
          error={overview.error}
          retry={overview.reload}
        />
      ) : (
        <>
          <div className="grid grid-3">
            <Metric
              label="SMEs onboarded"
              value={data.smes_onboarded.toLocaleString()}
              note="Current platform count"
            />
            <Metric
              label="Research events"
              value={data.research_events.toLocaleString()}
              note="Consent-filtered event count"
            />
            <Metric
              label="Channels observed"
              value={String(Object.keys(data.messages_by_channel).length)}
              note="From classified events"
            />
          </div>
          <div className="grid grid-3" style={{ marginTop: 18 }}>
            <Distribution
              title="Messages by channel"
              description="Counts returned by the overview endpoint."
              values={Object.entries(data.messages_by_channel).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Questions by intent"
              description="Primary-intent counts returned by the API."
              values={Object.entries(data.questions_by_intent).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Bumpa data usage"
              description="Recorded data-context labels."
              values={Object.entries(data.bumpa_data_usage).sort(
                (a, b) => b[1] - a[1],
              )}
            />
          </div>
          {events.status === "ready" && (
            <>
              <LiveDataBanner
                label="event completeness window"
                source={events.source}
                status={events.status}
                count={events.data?.length}
                error={events.error}
              />
              <Card padded>
                <div className="card-head">
                  <div>
                    <h2>Evidence completeness</h2>
                    <p>
                      Computed only from the loaded event window of{" "}
                      {events.data?.length ?? 0} records.
                    </p>
                  </div>
                </div>
                {[
                  [
                    "Primary intent present",
                    (events.data ?? []).filter((event) => event.primary_intent)
                      .length,
                  ],
                  [
                    "Business function present",
                    (events.data ?? []).filter(
                      (event) => event.business_function,
                    ).length,
                  ],
                  [
                    "AI help type present",
                    (events.data ?? []).filter((event) => event.ai_help_type)
                      .length,
                  ],
                  [
                    "Bumpa context present",
                    (events.data ?? []).filter((event) => event.bumpa_data_used)
                      .length,
                  ],
                ].map(([label, count]) => (
                  <div className="detail-row" key={String(label)}>
                    <span className="detail-label">{label}</span>
                    <span className="detail-value">
                      {events.data?.length
                        ? `${Math.round((Number(count) / events.data.length) * 100)}%`
                        : "—"}
                    </span>
                  </div>
                ))}
              </Card>
            </>
          )}
        </>
      )}
    </AppShell>
  );
}

export function Questions() {
  const resource = useApiResource<ResearchEvent[]>(
    "/research/questions",
    previewResearchEvents,
  );
  const [search, setSearch] = useState("");
  const [intent, setIntent] = useState("all");
  const [selected, setSelected] = useState<ResearchEvent | null>(null);
  const rows = useMemo(
    () =>
      (resource.data ?? []).filter((event) => {
        const matchesText =
          `${event.redacted_text ?? ""} ${event.primary_intent ?? ""} ${event.tenant_pseudonym}`
            .toLowerCase()
            .includes(search.toLowerCase());
        return (
          matchesText && (intent === "all" || event.primary_intent === intent)
        );
      }),
    [intent, resource.data, search],
  );
  const intents = [
    ...new Set(
      (resource.data ?? [])
        .map((event) => event.primary_intent)
        .filter(Boolean),
    ),
  ] as string[];
  return (
    <AppShell surface="research" title="Question log">
      <PageHeader
        title="Question log"
        description="Explore redacted question events and their persisted classifications."
      />
      <LiveDataBanner
        label="question events"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      {resource.status !== "ready" ? (
        <ResourceState
          status={resource.status}
          error={resource.error}
          retry={resource.reload}
        />
      ) : !resource.data?.length ? (
        <ResourceState
          status="ready"
          error={null}
          retry={resource.reload}
          empty="No questions in this window"
        />
      ) : (
        <>
          <Filters search={search} setSearch={setSearch}>
            <select
              className="filter-select"
              aria-label="Filter by intent"
              value={intent}
              onChange={(event) => setIntent(event.target.value)}
            >
              <option value="all">All intents</option>
              {intents.map((value) => (
                <option value={value} key={value}>
                  {titleCase(value)}
                </option>
              ))}
            </select>
          </Filters>
          {rows.length ? (
            <section className="card table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Pseudonym</th>
                    <th>Channel</th>
                    <th>Redacted question</th>
                    <th>Intent</th>
                    <th>Help type</th>
                    <th>Data used</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((event) => (
                    <tr
                      key={event.id}
                      onClick={() => setSelected(event)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>{formatDate(event.created_at)}</td>
                      <td className="table-primary">
                        {event.tenant_pseudonym}
                      </td>
                      <td>
                        <Badge>{titleCase(event.channel)}</Badge>
                      </td>
                      <td className="table-primary" style={{ maxWidth: 330 }}>
                        {event.redacted_text ?? "Redacted text unavailable"}
                      </td>
                      <td>{titleCase(event.primary_intent)}</td>
                      <td>{titleCase(event.ai_help_type)}</td>
                      <td>
                        <span className="tag">
                          {titleCase(event.bumpa_data_used)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ) : (
            <StatePanel
              type="empty"
              title="No matching questions"
              description="Clear or adjust the filters to see other research events."
            />
          )}
        </>
      )}
      {selected && (
        <Modal
          title="Research event detail"
          onClose={() => setSelected(null)}
          actions={
            <button
              className="button button-secondary"
              onClick={() => setSelected(null)}
            >
              Close
            </button>
          }
        >
          <div className="alert alert-info">
            Redacted event view · participant identity and raw text are not
            exposed.
          </div>
          <blockquote
            style={{ margin: "18px 0", font: "500 1.35rem/1.45 Georgia,serif" }}
          >
            “{selected.redacted_text ?? "Text unavailable"}”
          </blockquote>
          {[
            ["Tenant pseudonym", selected.tenant_pseudonym],
            ["Event type", titleCase(selected.event_type)],
            [
              "Classification",
              `${titleCase(selected.primary_intent)} · ${titleCase(selected.ai_help_type)}`,
            ],
            ["Business function", titleCase(selected.business_function)],
            ["Reasoning complexity", titleCase(selected.complexity)],
            ["Bumpa context", titleCase(selected.bumpa_data_used)],
            ["Recorded", formatDate(selected.created_at)],
          ].map(([label, value]) => (
            <div className="detail-row" key={label}>
              <span className="detail-label">{label}</span>
              <span className="detail-value">{value}</span>
            </div>
          ))}
        </Modal>
      )}
    </AppShell>
  );
}

export function Conversations() {
  const resource = useApiResource<ResearchConversationSummary[]>(
    "/research/conversations",
    previewResearchConversations,
  );
  const [selected, setSelected] = useState<ResearchConversationDetail | null>(
    null,
  );
  const [detailStatus, setDetailStatus] = useState<
    "idle" | "loading" | "error"
  >("idle");
  const [detailError, setDetailError] = useState<string | null>(null);

  async function openConversation(conversation: ResearchConversationSummary) {
    setSelected(null);
    setDetailError(null);
    setDetailStatus("loading");
    if (resource.source === "demo") {
      const fixture = previewResearchConversationDetails[conversation.id];
      if (fixture) {
        setSelected(fixture);
        setDetailStatus("idle");
        return;
      }
    }
    try {
      const detail = await apiRequest<ResearchConversationDetail>(
        `/research/conversations/${encodeURIComponent(conversation.id)}`,
      );
      setSelected(detail);
      setDetailStatus("idle");
    } catch (reason) {
      setDetailError(
        reason instanceof Error
          ? reason.message
          : "The conversation could not be loaded.",
      );
      setDetailStatus("error");
    }
  }

  return (
    <AppShell surface="research" title="Conversation log">
      <PageHeader
        title="Conversation log"
        description="Explore consented, pseudonymised research events grouped into multi-turn conversations."
      />
      <LiveDataBanner
        label="research conversations"
        source={resource.source}
        status={resource.status}
        error={resource.error}
        count={resource.data?.length}
      />
      <div className="alert alert-info">
        Only consented, redacted research events are shown. Tenant, participant,
        conversation and event identifiers are pseudonymised.
      </div>
      {resource.status !== "ready" ? (
        <ResourceState
          status={resource.status}
          error={resource.error}
          retry={resource.reload}
        />
      ) : !resource.data?.length ? (
        <ResourceState
          status="ready"
          error={null}
          retry={resource.reload}
          empty="No consented conversations yet"
        />
      ) : (
        <section className="card table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Last activity</th>
                <th>Conversation</th>
                <th>Tenant</th>
                <th>Channel</th>
                <th>Events</th>
                <th>Latest redacted event</th>
                <th>Top intent</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {resource.data.map((conversation) => (
                <tr key={conversation.id}>
                  <td>{formatDate(conversation.last_activity_at)}</td>
                  <td className="table-primary">{conversation.id}</td>
                  <td>{conversation.tenant_pseudonym ?? "Unavailable"}</td>
                  <td>
                    <Badge>{titleCase(conversation.channel)}</Badge>
                  </td>
                  <td>{conversation.event_count.toLocaleString()}</td>
                  <td className="table-primary" style={{ maxWidth: 340 }}>
                    {conversation.latest_redacted_text ?? "Text unavailable"}
                  </td>
                  <td>
                    {titleCase(Object.keys(conversation.primary_intents)[0])}
                  </td>
                  <td>
                    <button
                      className="button button-ghost button-small"
                      aria-label={`Open ${conversation.id}`}
                      onClick={() => void openConversation(conversation)}
                    >
                      Open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {detailStatus === "loading" && (
        <div style={{ marginTop: 18 }}>
          <StatePanel type="loading" />
        </div>
      )}
      {detailStatus === "error" && (
        <div style={{ marginTop: 18 }}>
          <StatePanel
            type="error"
            title="Conversation unavailable"
            description={detailError ?? undefined}
          />
        </div>
      )}
      {selected && (
        <Modal
          title="Pseudonymised conversation"
          onClose={() => setSelected(null)}
          actions={
            <button
              className="button button-secondary"
              onClick={() => setSelected(null)}
            >
              Close
            </button>
          }
        >
          <div className="alert alert-info">
            Research-safe event timeline · raw identities and raw message text
            are not available in this view.
          </div>
          {[
            ["Conversation pseudonym", selected.id],
            ["Tenant pseudonym", selected.tenant_pseudonym ?? "Unavailable"],
            [
              "Participant pseudonyms",
              selected.participant_pseudonyms.join(", ") || "None",
            ],
            ["Channel", titleCase(selected.channel)],
            ["Event count", String(selected.event_count)],
            ["Started", formatDate(selected.started_at)],
            ["Last activity", formatDate(selected.last_activity_at)],
          ].map(([label, value]) => (
            <div className="detail-row" key={label}>
              <span className="detail-label">{label}</span>
              <span className="detail-value">{value}</span>
            </div>
          ))}
          <div className="timeline" style={{ marginTop: 22 }}>
            {selected.events.map((event) => (
              <div className="timeline-item" key={event.id}>
                <strong>
                  {titleCase(event.primary_intent)} ·{" "}
                  {formatDate(event.created_at)}
                </strong>
                <p>{event.redacted_text ?? "Text unavailable"}</p>
                <span className="table-secondary">
                  {event.user_pseudonym ?? "Participant unavailable"} ·{" "}
                  {titleCase(event.ai_help_type)} ·{" "}
                  {titleCase(event.bumpa_data_used)}
                </span>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </AppShell>
  );
}

export function Classifications() {
  const taxonomy = useApiResource<Taxonomy>(
    "/research/taxonomy",
    previewTaxonomy,
  );
  const events = useApiResource<ResearchEvent[]>(
    "/research/events",
    previewResearchEvents,
  );
  const rows = events.data ?? [];
  const combinedStatus =
    taxonomy.status === "error" || events.status === "error"
      ? "error"
      : taxonomy.status === "loading" || events.status === "loading"
        ? "loading"
        : "ready";
  const combinedSource =
    taxonomy.source === "demo" || events.source === "demo"
      ? "demo"
      : taxonomy.source === "live" && events.source === "live"
        ? "live"
        : null;
  return (
    <AppShell surface="research" title="Classifications">
      <PageHeader
        title="Classification explorer"
        description="Inspect the live taxonomy and distributions computed from loaded research events."
      />
      <LiveDataBanner
        label="taxonomy and classification events"
        source={combinedSource}
        status={combinedStatus}
        error={taxonomy.error ?? events.error}
      />
      {taxonomy.status !== "ready" || !taxonomy.data ? (
        <ResourceState
          status={taxonomy.status}
          error={taxonomy.error}
          retry={taxonomy.reload}
        />
      ) : events.status !== "ready" ? (
        <ResourceState
          status={events.status}
          error={events.error}
          retry={events.reload}
        />
      ) : (
        <>
          <div className="grid grid-3">
            <Distribution
              title="Primary intent"
              description={`${rows.length} loaded events.`}
              values={countValues(rows, (event) => event.primary_intent)}
            />
            <Distribution
              title="Reasoning complexity"
              description="Classifier output in the loaded window."
              values={countValues(rows, (event) => event.complexity)}
            />
            <Distribution
              title="AI help type"
              description="Persisted assistance categories."
              values={countValues(rows, (event) => event.ai_help_type)}
            />
          </div>
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Taxonomy definitions</h2>
                <p>Values returned by the current taxonomy endpoint.</p>
              </div>
              <button className="button button-secondary button-small" disabled>
                Change log unavailable
              </button>
            </div>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Dimension</th>
                    <th>Configured values</th>
                    <th>Classified events</th>
                    <th>Unclassified events</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    [
                      "Primary intent",
                      taxonomy.data.primary_intent.length,
                      rows.filter((event) => event.primary_intent).length,
                    ],
                    [
                      "Business function",
                      taxonomy.data.business_function.length,
                      rows.filter((event) => event.business_function).length,
                    ],
                    [
                      "AI help type",
                      taxonomy.data.ai_help_type.length,
                      rows.filter((event) => event.ai_help_type).length,
                    ],
                    [
                      "Complexity",
                      taxonomy.data.complexity.length,
                      rows.filter((event) => event.complexity).length,
                    ],
                  ].map(([dimension, values, classified]) => (
                    <tr key={String(dimension)}>
                      <td className="table-primary">{dimension}</td>
                      <td>{values}</td>
                      <td>{classified}</td>
                      <td>{rows.length - Number(classified)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <div className="alert alert-info">
            Human review queues and taxonomy mutations are not exposed by the
            API, so no review count or editable state is shown.
          </div>
        </>
      )}
    </AppShell>
  );
}

export function Cohorts() {
  const resource = useApiResource<ResearchEvent[]>(
    "/research/events?limit=500",
    previewResearchEvents,
  );
  const groups = useMemo(() => {
    const map = new Map<string, ResearchEvent[]>();
    for (const event of resource.data ?? [])
      map.set(event.tenant_pseudonym, [
        ...(map.get(event.tenant_pseudonym) ?? []),
        event,
      ]);
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [resource.data]);
  return (
    <AppShell surface="research" title="Cohorts">
      <PageHeader
        title="Observed tenant cohorts"
        description="Compare pseudonymised event activity without claiming demographic or business-category data the API does not expose."
        actions={
          <button className="button button-primary" disabled>
            ＋ Define cohort unavailable
          </button>
        }
      />
      <LiveDataBanner
        label="cohort event window"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      {resource.status !== "ready" ? (
        <ResourceState
          status={resource.status}
          error={resource.error}
          retry={resource.reload}
        />
      ) : !groups.length ? (
        <ResourceState
          status="ready"
          error={null}
          retry={resource.reload}
          empty="No cohort evidence available"
        />
      ) : (
        <div className="grid grid-2">
          {groups.map(([pseudonym, events]) => {
            const channels = countValues(events, (event) => event.channel);
            const intents = countValues(
              events,
              (event) => event.primary_intent,
            );
            return (
              <Card padded key={pseudonym}>
                <div className="card-head">
                  <div>
                    <h2>{pseudonym}</h2>
                    <p>
                      Pseudonymised tenant · {events.length} event
                      {events.length === 1 ? "" : "s"}
                    </p>
                  </div>
                  <Badge>{formatDate(events[0]?.created_at)}</Badge>
                </div>
                <div className="grid grid-2">
                  <div>
                    <div className="metric-label">Top channel</div>
                    <div className="metric-value" style={{ fontSize: 22 }}>
                      {titleCase(channels[0]?.[0])}
                    </div>
                  </div>
                  <div>
                    <div className="metric-label">Top intent</div>
                    <div className="metric-value" style={{ fontSize: 22 }}>
                      {titleCase(intents[0]?.[0])}
                    </div>
                  </div>
                </div>
                <div
                  className="alert alert-info"
                  style={{ marginTop: 18, marginBottom: 0 }}
                >
                  This grouping uses only the tenant pseudonym present on loaded
                  research events.
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </AppShell>
  );
}

type ReportDetail = {
  id: string;
  status: string;
  title: string | null;
  summary: string | null;
  artifacts: Array<{
    format: string;
    byte_size: number;
    checksum_sha256: string;
  }>;
};

function ReportInventory({ mode }: { mode: "reports" | "exports" }) {
  const resource = useApiResource<Report[]>(
    "/research/reports",
    previewReports,
  );
  const [modal, setModal] = useState(false);
  const [type, setType] = useState(
    mode === "reports" ? "weekly_memo" : "sme_usage",
  );
  const [format, setFormat] = useState(mode === "reports" ? "pdf" : "csv");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await apiRequest<Report>(
        mode === "reports" ? "/research/reports" : "/research/exports",
        {
          method: "POST",
          body: JSON.stringify({
            report_type: type,
            filters: {},
            formats: [format],
          }),
        },
      );
      resource.replace([created, ...(resource.data ?? [])]);
      setModal(false);
      setToast(
        `${mode === "reports" ? "Report" : "Export"} queued. Its status will appear in the artifact list shortly.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The artifact could not be generated.",
      );
    } finally {
      setBusy(false);
    }
  };
  const inspect = async (id: string) => {
    setDetailBusy(true);
    setError("");
    try {
      setDetail(await apiRequest<ReportDetail>(`/research/reports/${id}`));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Report details could not be loaded.",
      );
    } finally {
      setDetailBusy(false);
    }
  };
  const noun = mode === "reports" ? "report" : "export";
  return (
    <AppShell
      surface="research"
      title={mode === "reports" ? "Reports" : "Exports"}
    >
      <PageHeader
        title={mode === "reports" ? "Research reports" : "Export centre"}
        description={
          mode === "reports"
            ? "Generate durable reports using the configured artifact adapter."
            : "Create anonymised, audit-logged research artifacts."
        }
        actions={
          <button
            className="button button-primary"
            disabled={resource.source !== "live"}
            title={
              resource.source !== "live"
                ? "Artifact creation requires a reachable live API."
                : undefined
            }
            onClick={() => setModal(true)}
          >
            ＋ Create {noun}
          </button>
        }
      />
      <LiveDataBanner
        label="research artifacts"
        source={resource.source}
        status={resource.status}
        count={resource.data?.length}
        error={resource.error}
      />
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {resource.status !== "ready" ? (
        <ResourceState
          status={resource.status}
          error={resource.error}
          retry={resource.reload}
        />
      ) : !resource.data?.length ? (
        <ResourceState
          status="ready"
          error={null}
          retry={resource.reload}
          empty={`No ${noun}s generated`}
        />
      ) : (
        <section className="card table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Artifact</th>
                <th>Type</th>
                <th>Created</th>
                <th>Finished</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {resource.data.map((report) => (
                <tr key={report.id}>
                  <td>
                    <div className="table-primary">
                      {report.title ?? titleCase(report.report_type)}
                    </div>
                    <div className="table-secondary">
                      {report.id.slice(0, 12)}
                    </div>
                  </td>
                  <td>{titleCase(report.report_type)}</td>
                  <td>{formatDate(report.created_at)}</td>
                  <td>{formatDate(report.finished_at)}</td>
                  <td>
                    <Badge>{titleCase(report.status)}</Badge>
                  </td>
                  <td>
                    <button
                      className="button button-ghost button-small"
                      disabled={detailBusy || resource.source !== "live"}
                      onClick={() => void inspect(report.id)}
                    >
                      {detailBusy ? "Loading…" : "Files →"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {modal && (
        <Modal
          title={`Create research ${noun}`}
          onClose={() => !busy && setModal(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => setModal(false)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                disabled={busy}
                onClick={() => void create()}
              >
                {busy ? "Generating…" : `Generate ${noun}`}
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="report-type">Artifact type</label>
            <select
              id="report-type"
              className="select"
              value={type}
              onChange={(event) => setType(event.target.value)}
            >
              <option value="sme_usage">SME usage</option>
              <option value="cohort_behavior">Cohort behaviour</option>
              <option value="question_taxonomy">Question taxonomy</option>
              <option value="weekly_memo">Weekly memo</option>
              <option value="monthly_memo">Monthly memo</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="report-format">Format</label>
            <select
              id="report-format"
              className="select"
              value={format}
              onChange={(event) => setFormat(event.target.value)}
            >
              <option value="csv">CSV</option>
              <option value="jsonl">JSONL</option>
              <option value="pdf">PDF</option>
            </select>
          </div>
          <div className="alert alert-info">
            Requests are consent-filtered and audit-logged. In production,
            generation runs on the durable report queue.
          </div>
        </Modal>
      )}
      {detail && (
        <Modal
          title={detail.title ?? "Artifact files"}
          onClose={() => setDetail(null)}
          actions={
            <button
              className="button button-secondary"
              onClick={() => setDetail(null)}
            >
              Close
            </button>
          }
        >
          <p>{detail.summary ?? "No summary was recorded."}</p>
          {detail.artifacts.length ? (
            detail.artifacts.map((artifact) => (
              <div className="detail-row" key={artifact.format}>
                <span>
                  <strong>{artifact.format.toUpperCase()}</strong>
                  <br />
                  <span className="table-secondary">
                    {artifact.byte_size.toLocaleString()} bytes · SHA-256{" "}
                    {artifact.checksum_sha256.slice(0, 12)}…
                  </span>
                </span>
                <a
                  className="button button-primary button-small"
                  href={`/api/backend/research/reports/${detail.id}/download/${artifact.format}`}
                >
                  Download
                </a>
              </div>
            ))
          ) : (
            <StatePanel
              type="empty"
              title="No files are available"
              description="The report metadata exists, but no generated artifact was returned."
            />
          )}
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

export function Reports() {
  return <ReportInventory mode="reports" />;
}
export function Exports() {
  return <ReportInventory mode="exports" />;
}
