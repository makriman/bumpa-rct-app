"use client";

import { AppIcon } from "@/components/app-icon";
import {
  formatDate,
  titleCase,
  type ResearchEvent,
  type ResearchOverviewData,
} from "@/lib/platform-data";
import { useApiResource, type ApiResource } from "@/lib/use-api-resource";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import { Card, Metric, PageHeader, ScrollableTable, StatePanel } from "./ui";

type CountEntry = [string, number];
type RankedEntry = { label: string; count: number };

function formatLatency(value: number | null): string {
  if (value === null) return "Not measured";
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
}

function sortedEntries(value: Record<string, number>): CountEntry[] {
  return Object.entries(value).sort((a, b) => b[1] - a[1]);
}

function downloadOverview(value: ResearchOverviewData) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "research-overview.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ResearchOverview() {
  const overview = useApiResource<ResearchOverviewData>("/research/overview");
  const events = useApiResource<ResearchEvent[]>("/research/events");
  const data = overview.data;
  return (
    <AppShell title="Research overview">
      <PageHeader
        title="Research overview"
        description="A consent-safe view of adoption, question patterns, data use, performance, retention, and research operations."
        actions={
          <button
            type="button"
            className="button button-secondary"
            disabled={!data}
            onClick={() => data && downloadOverview(data)}
          >
            <AppIcon name="download" size={16} /> Export loaded view
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
        <OverviewState resource={overview} />
      ) : (
        <OverviewContent data={data} events={events} />
      )}
    </AppShell>
  );
}

function OverviewState({
  resource,
}: {
  resource: ApiResource<ResearchOverviewData>;
}) {
  if (resource.status === "loading") return <StatePanel type="loading" />;
  return (
    <StatePanel
      type="error"
      description={resource.error ?? undefined}
      action={
        <button
          type="button"
          className="button button-secondary"
          onClick={() => void resource.reload()}
        >
          Try again
        </button>
      }
    />
  );
}

function OverviewContent({
  data,
  events,
}: {
  data: ResearchOverviewData;
  events: ApiResource<ResearchEvent[]>;
}) {
  return (
    <>
      <OverviewMetrics data={data} />
      <OverviewDistributions data={data} />
      <OverviewOperations data={data} />
      <RetentionCard data={data} />
      <RepeatUsageCard data={data} />
      <CommonQuestions data={data} />
      {events.status === "ready" && <EvidenceCompleteness events={events} />}
    </>
  );
}

function OverviewMetrics({ data }: { data: ResearchOverviewData }) {
  const primary = [
    [
      "SMEs onboarded",
      data.smes_onboarded.toLocaleString(),
      "All current workspaces",
    ],
    [
      "Active today",
      data.active_smes.day.toLocaleString(),
      "Consented SMEs · trailing 24 hours",
    ],
    [
      "Active this week",
      data.active_smes.week.toLocaleString(),
      "Consented SMEs · trailing 7 days",
    ],
    [
      "Active this month",
      data.active_smes.month.toLocaleString(),
      "Consented SMEs · trailing 30 days",
    ],
  ];
  const secondary = [
    [
      "Research events",
      data.research_events.toLocaleString(),
      "Consent-filtered evidence",
    ],
    [
      "Repeat usage",
      data.repeat_usage.repeat_rate_pct === null
        ? "Not measured"
        : `${data.repeat_usage.repeat_rate_pct}%`,
      `${data.repeat_usage.repeat_smes} of ${data.repeat_usage.smes_observed} observed SMEs used the assistant on 2+ days`,
    ],
    [
      "Hermes response p50",
      formatLatency(data.hermes_response_latency.p50_ms),
      `${data.hermes_response_latency.samples.toLocaleString()} measured responses`,
    ],
    [
      "Generated artifacts",
      data.exports.total.toLocaleString(),
      `${data.report_generation.total.toLocaleString()} report requests`,
    ],
  ];
  return (
    <>
      <div className="grid grid-4">
        {primary.map(([label, value, note]) => (
          <Metric key={label} label={label} value={value} note={note} />
        ))}
      </div>
      <div className="grid grid-4" style={{ marginTop: 18 }}>
        {secondary.map(([label, value, note]) => (
          <Metric key={label} label={label} value={value} note={note} />
        ))}
      </div>
    </>
  );
}

function OverviewDistributions({ data }: { data: ResearchOverviewData }) {
  const groups: Array<[string, string, CountEntry[]]> = [
    [
      "Research consent",
      "Current workspace participation choices.",
      sortedEntries(data.research_consent_status),
    ],
    [
      "Messages by channel",
      "Consent-filtered questions by product channel.",
      sortedEntries(data.messages_by_channel),
    ],
    [
      "Active users by channel",
      "Distinct consented users observed on each channel.",
      sortedEntries(data.active_users_by_channel),
    ],
    [
      "Questions by category",
      "Primary intent recorded for each consented question.",
      sortedEntries(data.questions_by_category),
    ],
    [
      "Business functions",
      "The operating area each question concerns.",
      sortedEntries(data.questions_by_business_function),
    ],
    [
      "Reasoning complexity",
      "Observed depth of assistance requested.",
      sortedEntries(data.questions_by_complexity),
    ],
    [
      "AI help type",
      "Lookup, diagnosis, recommendations, and other modes.",
      sortedEntries(data.questions_by_ai_help_type),
    ],
    [
      "Bumpa data used",
      "Business context recorded for each answer.",
      sortedEntries(data.bumpa_data_usage),
    ],
  ];
  return (
    <>
      {[groups.slice(0, 3), groups.slice(3, 6)].map((row) => (
        <div className="grid grid-3" style={{ marginTop: 18 }} key={row[0][0]}>
          {row.map(([title, description, values]) => (
            <Distribution
              key={title}
              title={title}
              description={description}
              values={values}
            />
          ))}
        </div>
      ))}
      <div className="grid grid-3" style={{ marginTop: 18 }}>
        {groups.slice(6).map(([title, description, values]) => (
          <Distribution
            key={title}
            title={title}
            description={description}
            values={values}
          />
        ))}
        <RankedList
          title="Recurring problem areas"
          description="The most frequently observed question categories."
          values={data.top_recurring_problems}
          classify
        />
      </div>
    </>
  );
}

function Distribution({
  title,
  description,
  values,
}: {
  title: string;
  description: string;
  values: CountEntry[];
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
        values.map(([label, count]) => (
          <div className="detail-row" key={label}>
            <span className="detail-label">{titleCase(label)}</span>
            <span className="detail-value">
              {count.toLocaleString()}
              {total ? ` · ${Math.round((count / total) * 100)}%` : ""}
            </span>
          </div>
        ))
      ) : (
        <p className="table-secondary">No consented evidence yet.</p>
      )}
    </Card>
  );
}

function OverviewOperations({ data }: { data: ResearchOverviewData }) {
  return (
    <div className="grid grid-3" style={{ marginTop: 18 }}>
      <OperationCard
        title="Hermes response latency"
        description="End-to-end runtime latency for consented conversations."
        rows={[
          ["Median (p50)", formatLatency(data.hermes_response_latency.p50_ms)],
          ["Tail (p95)", formatLatency(data.hermes_response_latency.p95_ms)],
          ["Average", formatLatency(data.hermes_response_latency.average_ms)],
          ["Samples", data.hermes_response_latency.samples.toLocaleString()],
        ]}
      />
      <OperationCard
        title="Bumpa sync freshness"
        description="Current sync age for consented connected SMEs."
        rows={[
          ["Connected SMEs", String(data.bumpa_sync_freshness.connected_smes)],
          ["Fresh · under 24h", String(data.bumpa_sync_freshness.fresh_24h)],
          ["Watch · 24–72h", String(data.bumpa_sync_freshness.stale_24_to_72h)],
          ["Overdue · 72h+", String(data.bumpa_sync_freshness.overdue_72h)],
          ["Never synced", String(data.bumpa_sync_freshness.never_synced)],
          [
            "Latest successful sync",
            formatDate(data.bumpa_sync_freshness.latest_sync_at),
          ],
        ]}
      />
      <OperationCard
        title="Research operations"
        description="Report requests and durable artifact generation."
        rows={[
          ["Report requests", String(data.report_generation.total)],
          ["Generated artifacts", String(data.exports.total)],
          ...sortedEntries(data.report_generation.by_status).map(
            ([label, value]): [string, string] => [
              `Reports · ${titleCase(label)}`,
              String(value),
            ],
          ),
          ...sortedEntries(data.exports.by_format).map(
            ([label, value]) =>
              [label.toUpperCase(), String(value)] as [string, string],
          ),
        ]}
      />
    </div>
  );
}

function OperationCard({
  title,
  description,
  rows,
}: {
  title: string;
  description: string;
  rows: Array<[string, string]>;
}) {
  return (
    <Card padded>
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      {rows.map(([label, value]) => (
        <div className="detail-row" key={label}>
          <span className="detail-label">{label}</span>
          <span className="detail-value">{value}</span>
        </div>
      ))}
    </Card>
  );
}

function RetentionCard({ data }: { data: ResearchOverviewData }) {
  return (
    <div style={{ marginTop: 18 }}>
      <Card padded>
        <div className="card-head">
          <div>
            <h2>Retention by first-observed cohort</h2>
            <p>
              An SME is retained when it returns at least 7 or 30 days after its
              first consented event. Immature cohorts remain unscored.
            </p>
          </div>
        </div>
        {data.retention_by_cohort.length ? (
          <ScrollableTable label="Retention by first-observed cohort">
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
          </ScrollableTable>
        ) : (
          <p className="table-secondary">
            Retention appears after the first consented SME interaction.
          </p>
        )}
      </Card>
    </div>
  );
}

function RepeatUsageCard({ data }: { data: ResearchOverviewData }) {
  return (
    <div style={{ marginTop: 18 }}>
      <Card padded>
        <div className="card-head">
          <div>
            <h2>Repeat usage by SME</h2>
            <p>
              Pseudonymous workspace activity, ranked by distinct active days
              and message volume.
            </p>
          </div>
        </div>
        {data.repeat_usage.by_sme.length ? (
          <ScrollableTable label="Repeat usage by SME">
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
          </ScrollableTable>
        ) : (
          <p className="table-secondary">No repeat-usage evidence yet.</p>
        )}
      </Card>
    </div>
  );
}

function CommonQuestions({ data }: { data: ResearchOverviewData }) {
  return (
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
  );
}

function RankedList({
  title,
  description,
  values,
  classify = false,
}: {
  title: string;
  description: string;
  values: RankedEntry[];
  classify?: boolean;
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
          <div className="detail-row" key={item.label}>
            <span className="detail-label">
              {index + 1}. {classify ? titleCase(item.label) : item.label}
            </span>
            <span className="detail-value">{item.count.toLocaleString()}</span>
          </div>
        ))
      ) : (
        <p className="table-secondary">
          No repeated pattern has reached this view.
        </p>
      )}
    </Card>
  );
}

function EvidenceCompleteness({
  events,
}: {
  events: ApiResource<ResearchEvent[]>;
}) {
  const loaded = events.data ?? [];
  const measures: Array<[string, number]> = [
    [
      "Primary intent present",
      loaded.filter((event) => event.primary_intent).length,
    ],
    [
      "Business function present",
      loaded.filter((event) => event.business_function).length,
    ],
    [
      "AI help type present",
      loaded.filter((event) => event.ai_help_type).length,
    ],
    [
      "Bumpa context present",
      loaded.filter((event) => event.bumpa_data_used).length,
    ],
  ];
  return (
    <>
      <LiveDataBanner
        label="event completeness window"
        source={events.source}
        status={events.status}
        count={loaded.length}
        error={events.error}
      />
      <Card padded>
        <div className="card-head">
          <div>
            <h2>Evidence completeness</h2>
            <p>
              Computed only from the loaded event window of {loaded.length}{" "}
              records.
            </p>
          </div>
        </div>
        {measures.map(([label, count]) => (
          <div className="detail-row" key={label}>
            <span className="detail-label">{label}</span>
            <span className="detail-value">
              {loaded.length
                ? `${Math.round((count / loaded.length) * 100)}%`
                : "—"}
            </span>
          </div>
        ))}
      </Card>
    </>
  );
}
