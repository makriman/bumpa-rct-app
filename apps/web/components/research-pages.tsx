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

function RankedList({
  title,
  description,
  values,
  empty = "No consented evidence has been observed yet.",
  classifyLabels = false,
}: {
  title: string;
  description: string;
  values: Array<{ label: string; count: number }>;
  empty?: string;
  classifyLabels?: boolean;
}) {
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      {values.length ? (
        values.map((item, index) => (
          <div className="detail-row" key={`${item.label}-${index}`}>
            <span className="detail-label">
              {index + 1}. {classifyLabels ? titleCase(item.label) : item.label}
            </span>
            <span className="detail-value">{item.count.toLocaleString()}</span>
          </div>
        ))
      ) : (
        <p className="table-secondary">{empty}</p>
      )}
    </Card>
  );
}

function formatLatency(value: number | null): string {
  if (value === null) return "Not measured";
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
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
        description="A consent-safe view of adoption, question patterns, data use, performance, retention, and research operations."
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
        Event-level measures include only SMEs with active research consent.
        Tenant activity is pseudonymised, question text is redacted, and raw
        identities are never requested by this view.
      </div>
      {overview.status !== "ready" || !data ? (
        <ResourceState
          status={overview.status}
          error={overview.error}
          retry={overview.reload}
        />
      ) : (
        <>
          <div className="grid grid-4">
            <Metric
              label="SMEs onboarded"
              value={data.smes_onboarded.toLocaleString()}
              note="All current workspaces"
            />
            <Metric
              label="Active today"
              value={data.active_smes.day.toLocaleString()}
              note="Consented SMEs · trailing 24 hours"
            />
            <Metric
              label="Active this week"
              value={data.active_smes.week.toLocaleString()}
              note="Consented SMEs · trailing 7 days"
            />
            <Metric
              label="Active this month"
              value={data.active_smes.month.toLocaleString()}
              note="Consented SMEs · trailing 30 days"
            />
          </div>
          <div className="grid grid-4" style={{ marginTop: 18 }}>
            <Metric
              label="Research events"
              value={data.research_events.toLocaleString()}
              note="Consent-filtered evidence"
            />
            <Metric
              label="Repeat usage"
              value={
                data.repeat_usage.repeat_rate_pct === null
                  ? "Not measured"
                  : `${data.repeat_usage.repeat_rate_pct}%`
              }
              note={`${data.repeat_usage.repeat_smes} of ${data.repeat_usage.smes_observed} observed SMEs used the assistant on 2+ days`}
            />
            <Metric
              label="Hermes response p50"
              value={formatLatency(data.hermes_response_latency.p50_ms)}
              note={`${data.hermes_response_latency.samples.toLocaleString()} measured responses`}
            />
            <Metric
              label="Generated artifacts"
              value={data.exports.total.toLocaleString()}
              note={`${data.report_generation.total.toLocaleString()} report requests`}
            />
          </div>

          <div className="grid grid-3" style={{ marginTop: 18 }}>
            <Distribution
              title="Research consent"
              description="Current workspace participation choices."
              values={Object.entries(data.research_consent_status).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Messages by channel"
              description="Consent-filtered questions by product channel."
              values={Object.entries(data.messages_by_channel).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Active users by channel"
              description="Distinct consented users observed on each channel."
              values={Object.entries(data.active_users_by_channel).sort(
                (a, b) => b[1] - a[1],
              )}
            />
          </div>

          <div className="grid grid-3" style={{ marginTop: 18 }}>
            <Distribution
              title="Questions by category"
              description="Primary intent recorded for each consented question."
              values={Object.entries(data.questions_by_category).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Business functions"
              description="The operating area each question concerns."
              values={Object.entries(data.questions_by_business_function).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Reasoning complexity"
              description="Observed depth of assistance requested."
              values={Object.entries(data.questions_by_complexity).sort(
                (a, b) => b[1] - a[1],
              )}
            />
          </div>

          <div className="grid grid-3" style={{ marginTop: 18 }}>
            <Distribution
              title="AI help type"
              description="Lookup, diagnosis, recommendations, and other modes."
              values={Object.entries(data.questions_by_ai_help_type).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <Distribution
              title="Bumpa data used"
              description="Business context recorded for each answer."
              values={Object.entries(data.bumpa_data_usage).sort(
                (a, b) => b[1] - a[1],
              )}
            />
            <RankedList
              title="Recurring problem areas"
              description="The most frequently observed question categories."
              values={data.top_recurring_problems}
              classifyLabels
            />
          </div>

          <div className="grid grid-3" style={{ marginTop: 18 }}>
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>Hermes response latency</h2>
                  <p>End-to-end runtime latency for consented conversations.</p>
                </div>
              </div>
              {[
                [
                  "Median (p50)",
                  formatLatency(data.hermes_response_latency.p50_ms),
                ],
                [
                  "Tail (p95)",
                  formatLatency(data.hermes_response_latency.p95_ms),
                ],
                [
                  "Average",
                  formatLatency(data.hermes_response_latency.average_ms),
                ],
                [
                  "Samples",
                  data.hermes_response_latency.samples.toLocaleString(),
                ],
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
                  <h2>Bumpa sync freshness</h2>
                  <p>Current sync age for consented connected SMEs.</p>
                </div>
              </div>
              {[
                ["Connected SMEs", data.bumpa_sync_freshness.connected_smes],
                ["Fresh · under 24h", data.bumpa_sync_freshness.fresh_24h],
                ["Watch · 24–72h", data.bumpa_sync_freshness.stale_24_to_72h],
                ["Overdue · 72h+", data.bumpa_sync_freshness.overdue_72h],
                ["Never synced", data.bumpa_sync_freshness.never_synced],
              ].map(([label, value]) => (
                <div className="detail-row" key={String(label)}>
                  <span className="detail-label">{label}</span>
                  <span className="detail-value">{value}</span>
                </div>
              ))}
              <p className="table-secondary" style={{ marginTop: 14 }}>
                Latest successful sync:{" "}
                {formatDate(data.bumpa_sync_freshness.latest_sync_at)}
              </p>
            </Card>
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>Research operations</h2>
                  <p>Report requests and durable artifact generation.</p>
                </div>
              </div>
              <div className="detail-row">
                <span className="detail-label">Report requests</span>
                <span className="detail-value">
                  {data.report_generation.total}
                </span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Generated artifacts</span>
                <span className="detail-value">{data.exports.total}</span>
              </div>
              {Object.entries(data.report_generation.by_status)
                .sort((a, b) => b[1] - a[1])
                .map(([status, count]) => (
                  <div className="detail-row" key={`status-${status}`}>
                    <span className="detail-label">
                      Reports · {titleCase(status)}
                    </span>
                    <span className="detail-value">{count}</span>
                  </div>
                ))}
              {Object.entries(data.report_generation.by_type)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 3)
                .map(([type, count]) => (
                  <div className="detail-row" key={`type-${type}`}>
                    <span className="detail-label">{titleCase(type)}</span>
                    <span className="detail-value">{count}</span>
                  </div>
                ))}
              {Object.entries(data.exports.by_format)
                .sort((a, b) => b[1] - a[1])
                .map(([format, count]) => (
                  <div className="detail-row" key={format}>
                    <span className="detail-label">{format.toUpperCase()}</span>
                    <span className="detail-value">{count}</span>
                  </div>
                ))}
            </Card>
          </div>

          <div style={{ marginTop: 18 }}>
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>Retention by first-observed cohort</h2>
                  <p>
                    An SME is retained when it returns at least 7 or 30 days
                    after its first consented event. Immature cohorts remain
                    unscored.
                  </p>
                </div>
              </div>
              {data.retention_by_cohort.length ? (
                <div className="table-wrap">
                  <table style={{ minWidth: 720 }}>
                    <thead>
                      <tr>
                        <th>Cohort</th>
                        <th>SMEs</th>
                        <th>7-day eligible</th>
                        <th>7-day retained</th>
                        <th>30-day eligible</th>
                        <th>30-day retained</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.retention_by_cohort.map((cohort) => (
                        <tr key={cohort.cohort}>
                          <td>{cohort.cohort}</td>
                          <td>{cohort.smes}</td>
                          <td>{cohort.eligible_7d}</td>
                          <td>
                            {cohort.retention_7d_pct === null
                              ? "Maturing"
                              : `${cohort.retention_7d_pct}%`}
                          </td>
                          <td>{cohort.eligible_30d}</td>
                          <td>
                            {cohort.retention_30d_pct === null
                              ? "Maturing"
                              : `${cohort.retention_30d_pct}%`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="table-secondary">
                  Retention appears after the first consented SME interaction.
                </p>
              )}
            </Card>
          </div>

          <div style={{ marginTop: 18 }}>
            <Card padded>
              <div className="card-head">
                <div>
                  <h2>Repeat usage by SME</h2>
                  <p>
                    Pseudonymous workspace activity, ranked by distinct active
                    days and message volume.
                  </p>
                </div>
              </div>
              {data.repeat_usage.by_sme.length ? (
                <div className="table-wrap">
                  <table style={{ minWidth: 680 }}>
                    <thead>
                      <tr>
                        <th>SME pseudonym</th>
                        <th>Active days</th>
                        <th>Events</th>
                        <th>First observed</th>
                        <th>Last observed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.repeat_usage.by_sme.map((sme) => (
                        <tr key={sme.tenant_pseudonym}>
                          <td>{sme.tenant_pseudonym}</td>
                          <td>{sme.active_days}</td>
                          <td>{sme.event_count}</td>
                          <td>{formatDate(sme.first_seen_at)}</td>
                          <td>{formatDate(sme.last_seen_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="table-secondary">No repeat-usage evidence yet.</p>
              )}
            </Card>
          </div>

          <div className="grid grid-2" style={{ marginTop: 18 }}>
            <RankedList
              title="Common sales questions"
              description="Repeated redacted questions about sales and revenue."
              values={data.most_common_sales_questions}
            />
            <RankedList
              title="Common inventory questions"
              description="Repeated redacted questions about stock and restocking."
              values={data.most_common_inventory_questions}
            />
            <RankedList
              title="Common customer questions"
              description="Repeated redacted questions about buyers and customer behaviour."
              values={data.most_common_customer_questions}
            />
            <RankedList
              title="Common advice requests"
              description="Recommendations, forecasts, teaching, and drafting requests."
              values={data.most_common_advice_requests}
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
              "Raw source retained",
              selected.raw_text_present
                ? "Yes — superadmin request required"
                : "No",
            ],
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
                  {titleCase(event.bumpa_data_used)} · Raw source{" "}
                  {event.raw_text_present ? "retained" : "not retained"}
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
  report_type: string;
  artifact_kind: "report" | "export";
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
    `/research/reports?artifact_kind=${mode.slice(0, -1)}`,
    previewReports,
  );
  const [modal, setModal] = useState(false);
  const [type, setType] = useState(
    mode === "reports" ? "weekly_memo" : "sme_usage",
  );
  const [formats, setFormats] = useState<string[]>([
    mode === "reports" ? "pdf" : "csv",
  ]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [tenantPseudonym, setTenantPseudonym] = useState("");
  const [channel, setChannel] = useState("");
  const [primaryIntent, setPrimaryIntent] = useState("");
  const [businessFunction, setBusinessFunction] = useState("");
  const [aiHelpType, setAiHelpType] = useState("");
  const [complexity, setComplexity] = useState("");
  const [accessReason, setAccessReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const raw = type === "raw_export_package";
  const visibleReports = (resource.data ?? []).filter(
    (report) =>
      !report.artifact_kind || report.artifact_kind === mode.slice(0, -1),
  );
  const selectedFilters = () =>
    Object.fromEntries(
      Object.entries({
        date_from: dateFrom,
        date_to: dateTo,
        tenant_pseudonym: tenantPseudonym,
        channel,
        primary_intent: primaryIntent,
        business_function: businessFunction,
        ai_help_type: aiHelpType,
        complexity,
      })
        .map(([key, value]) => [key, value.trim()])
        .filter(([, value]) => value),
    );
  const toggleFormat = (candidate: string) => {
    setFormats((current) =>
      current.includes(candidate)
        ? current.filter((item) => item !== candidate)
        : [...current, candidate],
    );
  };
  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await apiRequest<Report>(
        mode === "reports" ? "/research/reports" : "/research/exports",
        {
          method: "POST",
          headers: raw ? { "X-Access-Reason": accessReason.trim() } : undefined,
          body: JSON.stringify({
            report_type: type,
            filters: selectedFilters(),
            formats,
          }),
        },
      );
      resource.replace([created, ...(resource.data ?? [])]);
      if (raw) setAccessReason("");
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
      const loaded = await apiRequest<ReportDetail>(`/research/reports/${id}`);
      if (loaded.report_type === "raw_export_package") setAccessReason("");
      setDetail(loaded);
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
  const download = async (artifact: ReportDetail["artifacts"][number]) => {
    if (!detail) return;
    setDetailBusy(true);
    setError("");
    try {
      const isRaw = detail.report_type === "raw_export_package";
      const response = await fetch(
        `/api/backend/research/reports/${detail.id}/download/${artifact.format}`,
        {
          credentials: "same-origin",
          headers: isRaw
            ? { "X-Access-Reason": accessReason.trim() }
            : undefined,
        },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(
          payload?.detail ?? `Download failed (${response.status})`,
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `research-report-${detail.id}.${artifact.format}`;
      anchor.click();
      URL.revokeObjectURL(url);
      if (isRaw) setAccessReason("");
      setToast(
        `${artifact.format.toUpperCase()} integrity check passed and download started.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The download could not be completed.",
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
      ) : !visibleReports.length ? (
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
              {visibleReports.map((report) => (
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
                disabled={
                  busy ||
                  !formats.length ||
                  (raw && accessReason.trim().length < 12) ||
                  Boolean(dateFrom && dateTo && dateTo < dateFrom)
                }
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
              <option value="business_outcome_correlation">
                Business outcome correlation
              </option>
              <option value="weekly_memo">Weekly memo</option>
              <option value="monthly_memo">Monthly memo</option>
              {mode === "exports" && (
                <option value="anonymized_export_package">
                  Anonymized export package
                </option>
              )}
              {mode === "exports" && (
                <option value="raw_export_package">
                  Permissioned raw export (superadmin)
                </option>
              )}
            </select>
          </div>
          <div className="field">
            <label>Formats</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
              {[
                ["csv", "CSV"],
                ["jsonl", "JSONL"],
                ["pdf", "PDF"],
              ].map(([value, label]) => (
                <label
                  className="button button-secondary button-small"
                  key={value}
                >
                  <input
                    type="checkbox"
                    checked={formats.includes(value)}
                    onChange={() => toggleFormat(value)}
                  />{" "}
                  {label}
                </label>
              ))}
            </div>
            {!formats.length && (
              <span className="field-error">Select at least one format.</span>
            )}
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
              gap: 12,
            }}
          >
            <div className="field">
              <label htmlFor="report-date-from">From date</label>
              <input
                id="report-date-from"
                className="input"
                type="date"
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="report-date-to">To date</label>
              <input
                id="report-date-to"
                className="input"
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="tenant-pseudonym">SME pseudonym</label>
            <input
              id="tenant-pseudonym"
              className="input"
              value={tenantPseudonym}
              placeholder="Optional exact pseudonym"
              onChange={(event) => setTenantPseudonym(event.target.value)}
            />
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
              gap: 12,
            }}
          >
            <div className="field">
              <label htmlFor="report-channel">Channel</label>
              <select
                id="report-channel"
                className="select"
                value={channel}
                onChange={(event) => setChannel(event.target.value)}
              >
                <option value="">All channels</option>
                <option value="web">Web</option>
                <option value="whatsapp">WhatsApp</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="report-intent">Question category</label>
              <select
                id="report-intent"
                className="select"
                value={primaryIntent}
                onChange={(event) => setPrimaryIntent(event.target.value)}
              >
                <option value="">All categories</option>
                {previewTaxonomy.primary_intent.map((item) => (
                  <option value={item} key={item}>
                    {titleCase(item)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="report-function">Business function</label>
              <select
                id="report-function"
                className="select"
                value={businessFunction}
                onChange={(event) => setBusinessFunction(event.target.value)}
              >
                <option value="">All functions</option>
                {previewTaxonomy.business_function.map((item) => (
                  <option value={item} key={item}>
                    {titleCase(item)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="report-help-type">AI help type</label>
              <select
                id="report-help-type"
                className="select"
                value={aiHelpType}
                onChange={(event) => setAiHelpType(event.target.value)}
              >
                <option value="">All help types</option>
                {previewTaxonomy.ai_help_type.map((item) => (
                  <option value={item} key={item}>
                    {titleCase(item)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="report-complexity">Complexity</label>
              <select
                id="report-complexity"
                className="select"
                value={complexity}
                onChange={(event) => setComplexity(event.target.value)}
              >
                <option value="">All complexity levels</option>
                {previewTaxonomy.complexity.map((item) => (
                  <option value={item} key={item}>
                    {titleCase(item)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {raw && (
            <div className="field">
              <label htmlFor="raw-export-reason">Required access reason</label>
              <textarea
                id="raw-export-reason"
                className="textarea"
                minLength={12}
                maxLength={240}
                value={accessReason}
                placeholder="Explain the approved research purpose without personal data."
                onChange={(event) => setAccessReason(event.target.value)}
              />
              <span className="field-help">
                Raw packages are superadmin-only. Creation and every download
                are separately audited.
              </span>
            </div>
          )}
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
          {detail.report_type === "raw_export_package" && (
            <div className="field">
              <label htmlFor="raw-download-reason">Fresh download reason</label>
              <textarea
                id="raw-download-reason"
                className="textarea"
                minLength={12}
                maxLength={240}
                value={accessReason}
                placeholder="State the approved research purpose for this download."
                onChange={(event) => setAccessReason(event.target.value)}
              />
              <span className="field-help">
                Each raw download requires and records a fresh justification.
              </span>
            </div>
          )}
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
                <button
                  className="button button-primary button-small"
                  disabled={
                    detailBusy ||
                    (detail.report_type === "raw_export_package" &&
                      accessReason.trim().length < 12)
                  }
                  onClick={() => void download(artifact)}
                >
                  {detailBusy ? "Checking…" : "Download"}
                </button>
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
