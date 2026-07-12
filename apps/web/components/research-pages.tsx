"use client";

import { useMemo, useState } from "react";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import {
  Badge,
  Card,
  Chart,
  DemoStateToggle,
  Filters,
  Metric,
  Modal,
  PageHeader,
  StatePanel,
  Toast,
} from "./ui";
import { chartValues, reports, researchQuestions } from "@/lib/demo-data";

type DemoState = "ready" | "loading" | "empty" | "error";
function State({ state, label }: { state: DemoState; label: string }) {
  return state === "loading" ? (
    <StatePanel type="loading" />
  ) : state === "empty" ? (
    <StatePanel
      type="empty"
      title={`No ${label} in this range`}
      description="Adjust your filters or choose a wider date range."
    />
  ) : state === "error" ? (
    <StatePanel
      type="error"
      action={<button className="button button-secondary">Try again</button>}
    />
  ) : null;
}
const FilterBar = () => (
  <div className="card filters">
    <select className="filter-select" aria-label="Date range">
      <option>Last 30 days</option>
      <option>Last 7 days</option>
      <option>Quarter to date</option>
    </select>
    <select className="filter-select" aria-label="Tenant">
      <option>All consented SMEs</option>
      <option>SME–K4H2</option>
      <option>SME–M8P1</option>
    </select>
    <select className="filter-select" aria-label="Channel">
      <option>All channels</option>
      <option>WhatsApp</option>
      <option>Web</option>
    </select>
    <button className="button button-secondary button-small">Reset</button>
  </div>
);

export function ResearchOverview() {
  return (
    <AppShell surface="research" title="Research overview">
      <PageHeader
        title="Research overview"
        description="Pseudonymised evidence on how SMEs use AI to make business decisions."
        actions={
          <button className="button button-secondary">⇩ Export view</button>
        }
      />
      <LiveDataBanner endpoint="/research/overview" label="research overview" />
      <div className="alert alert-info">
        You are viewing redacted research data. Raw text access is
        permission-controlled and every reveal is audit logged.
      </div>
      <FilterBar />
      <div className="grid grid-4">
        <Metric
          label="Consented SMEs"
          value="21 / 24"
          trend="+2"
          note="87.5% participation"
          bars={[55, 58, 62, 66, 75, 88]}
        />
        <Metric
          label="Active SMEs · 30d"
          value="19"
          trend="+12%"
          note="90% of consented cohort"
          bars={[40, 44, 58, 55, 70, 83]}
        />
        <Metric
          label="Questions asked"
          value="12,486"
          trend="+18%"
          note="7,948 distinct conversations"
          bars={[35, 51, 48, 63, 72, 91]}
        />
        <Metric
          label="Repeat usage"
          value="73%"
          trend="+5%"
          note="Returned within 4 weeks"
          bars={[51, 55, 59, 62, 68, 73]}
        />
      </div>
      <div className="grid grid-2" style={{ marginTop: 18 }}>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Questions over time</h2>
              <p>Weekly volume by primary channel.</p>
            </div>
            <Badge tone="success">Complete</Badge>
          </div>
          <Chart
            values={chartValues}
            labels={[
              "W17",
              "18",
              "19",
              "20",
              "21",
              "22",
              "23",
              "24",
              "25",
              "26",
              "27",
              "28",
            ]}
            alt
          />
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>AI help type</h2>
              <p>How owners are using the assistant.</p>
            </div>
          </div>
          <div className="donut-wrap">
            <div
              className="donut"
              aria-label="Donut chart: data lookup 48%, recommendation 28%, diagnosis 14%, other 10%"
            />
            <div className="legend">
              <span>Data lookup · 48%</span>
              <span>Recommendation · 28%</span>
              <span>Diagnosis · 14%</span>
              <span>Other · 10%</span>
            </div>
          </div>
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Business functions</h2>
              <p>Share of classified questions.</p>
            </div>
          </div>
          {[
            ["Sales", 72, "32%"],
            ["Stock", 51, "23%"],
            ["Customers", 34, "15%"],
            ["Finance", 25, "11%"],
            ["Strategy", 20, "9%"],
          ].map(([n, w, p]) => (
            <div
              key={String(n)}
              style={{
                display: "grid",
                gridTemplateColumns: "90px 1fr 42px",
                alignItems: "center",
                gap: 10,
                marginBottom: 15,
                fontSize: 13,
              }}
            >
              <strong>{n}</strong>
              <div className="progress">
                <span style={{ width: `${w}%` }} />
              </div>
              <span>{p}</span>
            </div>
          ))}
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Evidence quality</h2>
              <p>Instrumentation completeness · last 30 days.</p>
            </div>
          </div>
          <div className="detail-row">
            <span className="detail-label">Events classified</span>
            <span className="detail-value">98.7%</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Bumpa context recorded</span>
            <span className="detail-value">99.4%</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">PII redacted</span>
            <span className="detail-value">100%</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Flagged for review</span>
            <span className="detail-value">2.1%</span>
          </div>
        </Card>
      </div>
    </AppShell>
  );
}

export function Questions() {
  const [search, setSearch] = useState("");
  const [state, setState] = useState<DemoState>("ready");
  const [selected, setSelected] = useState<
    (typeof researchQuestions)[number] | null
  >(null);
  const rows = useMemo(
    () =>
      researchQuestions.filter((q) =>
        `${q.question} ${q.intent} ${q.tenant}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [search],
  );
  return (
    <AppShell surface="research" title="Question log">
      <PageHeader
        title="Question log"
        description="Explore redacted SME questions and their research classifications."
        actions={<DemoStateToggle state={state} setState={setState} />}
      />
      {state !== "ready" ? (
        <State state={state} label="questions" />
      ) : (
        <>
          <Filters search={search} setSearch={setSearch}>
            <select className="filter-select">
              <option>All intents</option>
              <option>Sales analysis</option>
              <option>Inventory</option>
            </select>
            <select className="filter-select">
              <option>All help types</option>
              <option>Data lookup</option>
              <option>Recommendation</option>
            </select>
          </Filters>
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
                  <th>Latency</th>
                  <th>Quality</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((q) => (
                  <tr
                    key={`${q.time}-${q.user}`}
                    onClick={() => setSelected(q)}
                    style={{ cursor: "pointer" }}
                  >
                    <td>{q.time}</td>
                    <td>
                      <div className="table-primary">{q.tenant}</div>
                      <div className="table-secondary">{q.user}</div>
                    </td>
                    <td>
                      <Badge>{q.channel}</Badge>
                    </td>
                    <td className="table-primary" style={{ maxWidth: 300 }}>
                      {q.question}
                    </td>
                    <td>{q.intent}</td>
                    <td>{q.help}</td>
                    <td>
                      <span className="tag">{q.data}</span>
                    </td>
                    <td>{q.latency}</td>
                    <td>
                      <Badge>{q.flag}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
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
            Redacted view · raw text is not available to this role.
          </div>
          <blockquote
            style={{ margin: "18px 0", font: "500 1.35rem/1.45 Georgia,serif" }}
          >
            “{selected.question}”
          </blockquote>
          <div className="detail-row">
            <span className="detail-label">Tenant / user</span>
            <span className="detail-value">
              {selected.tenant} / {selected.user}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Classification</span>
            <span className="detail-value">
              {selected.intent} · {selected.help}
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Bumpa context</span>
            <span className="detail-value">{selected.data}</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Response latency</span>
            <span className="detail-value">{selected.latency}</span>
          </div>
        </Modal>
      )}
    </AppShell>
  );
}

export function Conversations() {
  const [search, setSearch] = useState("");
  const rows = [
    {
      id: "C–9184",
      tenant: "SME–K4H2",
      user: "U–104",
      started: "12 Jul · 10:42",
      channel: "WhatsApp",
      messages: 6,
      topic: "Sales performance",
      followup: "Yes",
    },
    {
      id: "C–9172",
      tenant: "SME–M8P1",
      user: "U–088",
      started: "12 Jul · 09:18",
      channel: "Web",
      messages: 4,
      topic: "Revenue diagnosis",
      followup: "Yes",
    },
    {
      id: "C–9104",
      tenant: "SME–B2N7",
      user: "U–121",
      started: "11 Jul · 18:04",
      channel: "WhatsApp",
      messages: 2,
      topic: "Customer marketing",
      followup: "No",
    },
  ].filter((r) =>
    `${r.id} ${r.tenant} ${r.topic}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <AppShell surface="research" title="Conversation log">
      <PageHeader
        title="Conversation log"
        description="Study multi-turn behaviour without exposing participant identities."
      />
      <Filters search={search} setSearch={setSearch}>
        <select className="filter-select">
          <option>All channels</option>
          <option>WhatsApp</option>
          <option>Web</option>
        </select>
      </Filters>
      <section className="card table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Conversation</th>
              <th>Tenant / user</th>
              <th>Started</th>
              <th>Channel</th>
              <th>Messages</th>
              <th>Primary topic</th>
              <th>Follow-up</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="table-primary">{r.id}</td>
                <td>
                  {r.tenant} / {r.user}
                </td>
                <td>{r.started}</td>
                <td>
                  <Badge>{r.channel}</Badge>
                </td>
                <td>{r.messages}</td>
                <td>{r.topic}</td>
                <td>{r.followup}</td>
                <td>
                  <button className="button button-ghost button-small">
                    Open redacted →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </AppShell>
  );
}

export function Classifications() {
  return (
    <AppShell surface="research" title="Classifications">
      <PageHeader
        title="Classification explorer"
        description="Inspect taxonomy coverage, distributions, and review queues."
      />
      <FilterBar />
      <div className="grid grid-3">
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Primary intent</h2>
              <p>Top five of eleven values.</p>
            </div>
          </div>
          {[
            ["Sales analysis", "28%", 80],
            ["Inventory management", "21%", 60],
            ["Customer management", "16%", 46],
            ["Finance", "12%", 34],
            ["Marketing", "9%", 26],
          ].map(([n, p, w]) => (
            <div key={String(n)} style={{ marginBottom: 15 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: 13,
                  marginBottom: 6,
                }}
              >
                <span>{n}</span>
                <strong>{p}</strong>
              </div>
              <div className="progress">
                <span style={{ width: `${w}%` }} />
              </div>
            </div>
          ))}
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Reasoning complexity</h2>
              <p>Classifier output distribution.</p>
            </div>
          </div>
          <div className="donut-wrap" style={{ gap: 18 }}>
            <div
              className="donut"
              style={{
                width: 130,
                height: 130,
                background:
                  "conic-gradient(var(--forest) 0 39%, var(--coral) 39% 70%, var(--amber) 70% 91%, #d8d5cb 91%)",
              }}
            />
            <div className="legend">
              <span>Simple lookup · 39%</span>
              <span>Single-step · 31%</span>
              <span>Multi-step · 21%</span>
              <span>Strategic · 9%</span>
            </div>
          </div>
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Review queue</h2>
              <p>Items requiring human validation.</p>
            </div>
            <Badge tone="warning">263 open</Badge>
          </div>
          <div className="detail-row">
            <span className="detail-label">Low confidence</span>
            <span className="detail-value">141</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Conflicting labels</span>
            <span className="detail-value">68</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Possible PII</span>
            <span className="detail-value">31</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Other</span>
            <span className="detail-value">23</span>
          </div>
          <button className="button button-secondary" style={{ marginTop: 14 }}>
            Open review queue
          </button>
        </Card>
      </div>
      <Card padded className="">
        <div className="card-head">
          <div>
            <h2>Taxonomy definitions</h2>
            <p>Version 1.3 · active since 1 July 2026.</p>
          </div>
          <button className="button button-secondary button-small">
            View change log
          </button>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Dimension</th>
                <th>Values</th>
                <th>Coverage</th>
                <th>Unclassified</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="table-primary">Primary intent</td>
                <td>11</td>
                <td>98.7%</td>
                <td>162</td>
              </tr>
              <tr>
                <td className="table-primary">Business function</td>
                <td>9</td>
                <td>99.1%</td>
                <td>112</td>
              </tr>
              <tr>
                <td className="table-primary">AI help type</td>
                <td>8</td>
                <td>98.9%</td>
                <td>137</td>
              </tr>
              <tr>
                <td className="table-primary">Complexity</td>
                <td>4</td>
                <td>99.4%</td>
                <td>75</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
    </AppShell>
  );
}

export function Cohorts() {
  const cohorts = [
    {
      name: "Fashion SMEs",
      members: 8,
      active: "88%",
      retention: "76%",
      messages: "4,212",
      note: "Highest use of marketing drafts",
    },
    {
      name: "Home & living",
      members: 5,
      active: "100%",
      retention: "81%",
      messages: "2,948",
      note: "Strongest inventory usage",
    },
    {
      name: "Food & drink",
      members: 4,
      active: "75%",
      retention: "63%",
      messages: "1,884",
      note: "Frequent daily sales lookups",
    },
    {
      name: "Newly onboarded · Jun",
      members: 6,
      active: "83%",
      retention: "—",
      messages: "1,106",
      note: "Week-four retention pending",
    },
  ];
  return (
    <AppShell surface="research" title="Cohorts">
      <PageHeader
        title="Cohort analysis"
        description="Compare adoption and repeat behaviour across transparent SME segments."
        actions={
          <button className="button button-primary">＋ Define cohort</button>
        }
      />
      <FilterBar />
      <div className="grid grid-2">
        {cohorts.map((c) => (
          <Card padded key={c.name}>
            <div className="card-head">
              <div>
                <h2>{c.name}</h2>
                <p>{c.members} consented SMEs</p>
              </div>
              <button className="button button-ghost button-small">
                Explore →
              </button>
            </div>
            <div className="grid grid-3">
              <div>
                <div className="metric-label">Active · 30d</div>
                <div className="metric-value" style={{ fontSize: 22 }}>
                  {c.active}
                </div>
              </div>
              <div>
                <div className="metric-label">W4 retention</div>
                <div className="metric-value" style={{ fontSize: 22 }}>
                  {c.retention}
                </div>
              </div>
              <div>
                <div className="metric-label">Questions</div>
                <div className="metric-value" style={{ fontSize: 22 }}>
                  {c.messages}
                </div>
              </div>
            </div>
            <div
              className="alert alert-info"
              style={{ marginTop: 18, marginBottom: 0 }}
            >
              {c.note}
            </div>
          </Card>
        ))}
      </div>
    </AppShell>
  );
}

export function Reports() {
  const [modal, setModal] = useState(false);
  const [toast, setToast] = useState("");
  return (
    <AppShell surface="research" title="Reports">
      <PageHeader
        title="Research reports"
        description="Generate durable, filtered reports with redacted examples and methods metadata."
        actions={
          <button
            className="button button-primary"
            onClick={() => setModal(true)}
          >
            ＋ Generate report
          </button>
        }
      />
      <div className="alert alert-info">
        Reports are generated asynchronously. You can leave this page and return
        when the artifact is ready.
      </div>
      <section className="card table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Report</th>
              <th>Type</th>
              <th>Scope</th>
              <th>Created</th>
              <th>Status</th>
              <th>Formats</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => (
              <tr key={r.title}>
                <td className="table-primary">{r.title}</td>
                <td>{r.type}</td>
                <td>{r.scope}</td>
                <td>{r.created}</td>
                <td>
                  <Badge>{r.status}</Badge>
                </td>
                <td>{r.formats}</td>
                <td>
                  <button
                    className="button button-ghost button-small"
                    disabled={r.status !== "Ready"}
                  >
                    Download
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {modal && (
        <Modal
          title="Generate research report"
          onClose={() => setModal(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setModal(false)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                onClick={() => {
                  setModal(false);
                  setToast("Report queued. We will update its status here.");
                }}
              >
                Queue report
              </button>
            </>
          }
        >
          <div className="field">
            <label>Report type</label>
            <select className="select">
              <option>Weekly research memo</option>
              <option>SME usage report</option>
              <option>Cohort behaviour report</option>
              <option>AI question taxonomy report</option>
              <option>Academic-style memo</option>
            </select>
          </div>
          <div className="grid grid-2">
            <div className="field">
              <label>Date from</label>
              <input className="input" type="date" defaultValue="2026-06-12" />
            </div>
            <div className="field">
              <label>Date to</label>
              <input className="input" type="date" defaultValue="2026-07-12" />
            </div>
          </div>
          <div className="field">
            <label>Population</label>
            <select className="select">
              <option>All consented SMEs</option>
              <option>Fashion cohort</option>
              <option>Home & living cohort</option>
            </select>
          </div>
          <div className="check-row">
            <input id="examples" type="checkbox" defaultChecked />
            <label htmlFor="examples">
              Include redacted examples where permitted.
            </label>
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

export function Exports() {
  const [modal, setModal] = useState(false);
  const [toast, setToast] = useState("");
  const exports = [
    {
      name: "anonymized_questions_2026-07-12.csv",
      type: "Anonymized",
      rows: "12,486",
      size: "4.8 MB",
      created: "12 Jul · 11:04",
      expires: "19 Jul",
    },
    {
      name: "taxonomy_q2_2026.jsonl",
      type: "Anonymized",
      rows: "28,941",
      size: "18.2 MB",
      created: "4 Jul · 15:18",
      expires: "Expired",
    },
    {
      name: "usage_cohort_fashion.csv",
      type: "Aggregate",
      rows: "1,408",
      size: "892 KB",
      created: "1 Jul · 09:42",
      expires: "15 Jul",
    },
  ];
  return (
    <AppShell surface="research" title="Exports">
      <PageHeader
        title="Export centre"
        description="Create time-limited, audit-logged research datasets."
        actions={
          <button
            className="button button-primary"
            onClick={() => setModal(true)}
          >
            ＋ Create export
          </button>
        }
      />
      <div className="alert alert-warning">
        Exports exclude raw PII by default. Downloaded files remain sensitive
        research material and must follow the approved storage policy.
      </div>
      <section className="card table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Privacy level</th>
              <th>Rows</th>
              <th>Size</th>
              <th>Created</th>
              <th>Expires</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {exports.map((e) => (
              <tr key={e.name}>
                <td className="table-primary">{e.name}</td>
                <td>
                  <Badge tone={e.type === "Aggregate" ? "info" : "success"}>
                    {e.type}
                  </Badge>
                </td>
                <td>{e.rows}</td>
                <td>{e.size}</td>
                <td>{e.created}</td>
                <td>{e.expires}</td>
                <td>
                  <button
                    className="button button-ghost button-small"
                    disabled={e.expires === "Expired"}
                  >
                    Download
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {modal && (
        <Modal
          title="Create anonymized export"
          onClose={() => setModal(false)}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setModal(false)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                onClick={() => {
                  setModal(false);
                  setToast("Export queued with default redaction.");
                }}
              >
                Create export
              </button>
            </>
          }
        >
          <div className="field">
            <label>Dataset</label>
            <select className="select">
              <option>Question events</option>
              <option>Conversation summaries</option>
              <option>Classification events</option>
              <option>Aggregate usage</option>
            </select>
          </div>
          <div className="field">
            <label>Format</label>
            <select className="select">
              <option>CSV</option>
              <option>JSONL</option>
            </select>
          </div>
          <div className="field">
            <label>Privacy level</label>
            <select className="select">
              <option>Anonymized · recommended</option>
              <option>Aggregate only</option>
            </select>
          </div>
          <div
            className="alert alert-success"
            style={{ marginTop: 18, marginBottom: 0 }}
          >
            ✓ PII redaction and consent filters are enabled.
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
