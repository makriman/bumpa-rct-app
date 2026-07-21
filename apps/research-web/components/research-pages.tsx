"use client";

import { useMemo, useState } from "react";
import { AppIcon } from "@/components/app-icon";
import { apiRequest } from "@/lib/api";
import {
  countValues,
  formatDate,
  titleCase,
  type ResearchConversationDetail,
  type ResearchConversationSummary,
  type ResearchEvent,
  type Taxonomy,
} from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";
import { usePersistentFilters } from "@/lib/use-persistent-filters";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import {
  Badge,
  Card,
  Filters,
  Modal,
  PageHeader,
  ScrollableTable,
  StatePanel,
} from "./ui";

const QUESTION_FILTERS = {
  q: { defaultValue: "" },
  intent: { defaultValue: "all" },
} as const;

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
            type="button"
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

export { ResearchOverview } from "./research-overview";

export function Questions() {
  const resource = useApiResource<ResearchEvent[]>("/research/questions");
  const { values: filters, setFilter } = usePersistentFilters(QUESTION_FILTERS);
  const { q: search, intent } = filters;
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
      (resource.data ?? []).flatMap((event) =>
        event.primary_intent ? [event.primary_intent] : [],
      ),
    ),
  ];
  return (
    <AppShell title="Question log">
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
          <Filters search={search} setSearch={(value) => setFilter("q", value)}>
            <select
              className="filter-select"
              aria-label="Filter by intent"
              value={intent}
              onChange={(event) => setFilter("intent", event.target.value)}
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
            <ScrollableTable className="card" label="Research question events">
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
                    <tr key={event.id}>
                      <td>{formatDate(event.created_at)}</td>
                      <td className="table-primary">
                        {event.tenant_pseudonym}
                      </td>
                      <td>
                        <Badge>{titleCase(event.channel)}</Badge>
                      </td>
                      <td className="table-primary" style={{ maxWidth: 330 }}>
                        <button
                          type="button"
                          className="table-action table-row-action"
                          onClick={() => setSelected(event)}
                        >
                          {event.redacted_text ?? "Redacted text unavailable"}
                        </button>
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
            </ScrollableTable>
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
              type="button"
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
    <AppShell title="Conversation log">
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
        <ScrollableTable className="card" label="Research conversations">
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
                      type="button"
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
        </ScrollableTable>
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
              type="button"
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
  const taxonomy = useApiResource<Taxonomy>("/research/taxonomy");
  const events = useApiResource<ResearchEvent[]>("/research/events");
  const rows = events.data ?? [];
  const combinedStatus =
    taxonomy.status === "error" || events.status === "error"
      ? "error"
      : taxonomy.status === "loading" || events.status === "loading"
        ? "loading"
        : "ready";
  const combinedSource =
    taxonomy.source === "live" && events.source === "live" ? "live" : null;
  return (
    <AppShell title="Classifications">
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
              <button
                type="button"
                className="button button-secondary button-small"
                disabled
              >
                Change log unavailable
              </button>
            </div>
            <ScrollableTable label="Research taxonomy definitions">
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
            </ScrollableTable>
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
    <AppShell title="Cohorts">
      <PageHeader
        title="Observed tenant cohorts"
        description="Compare pseudonymised event activity without claiming demographic or business-category data the API does not expose."
        actions={
          <button type="button" className="button button-primary" disabled>
            <AppIcon name="add" size={16} /> Define cohort unavailable
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

export { Exports, Reports } from "./research-artifacts";
