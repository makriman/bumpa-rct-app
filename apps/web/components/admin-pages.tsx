"use client";

import Link from "next/link";
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
  PageHeader,
  StatePanel,
  Toast,
} from "./ui";
import {
  chartValues,
  errors,
  statusTone,
  syncRuns,
  tenants,
  usageRows,
} from "@/lib/demo-data";

type DemoState = "ready" | "loading" | "empty" | "error";

function States({
  state,
  emptyTitle,
}: {
  state: DemoState;
  emptyTitle: string;
}) {
  if (state === "loading") return <StatePanel type="loading" />;
  if (state === "empty") return <StatePanel type="empty" title={emptyTitle} />;
  if (state === "error")
    return (
      <StatePanel
        type="error"
        action={<button className="button button-secondary">Try again</button>}
      />
    );
  return null;
}

export function AdminOverview() {
  return (
    <AppShell surface="admin" title="Operations overview">
      <PageHeader
        title="Good morning, Nneka."
        description="Here is the health of Bumpa Bestie across every active SME."
        actions={
          <Link className="button button-primary" href="/admin/onboarding">
            ＋ Onboard SME
          </Link>
        }
      />
      <LiveDataBanner endpoint="/admin/tenants" label="admin tenants" />
      <div className="grid grid-4">
        <Metric
          label="Active SMEs"
          value="24"
          trend="+3"
          note="21 consented to research"
          bars={[35, 44, 52, 55, 68, 72]}
        />
        <Metric
          label="Messages · 7 days"
          value="3,842"
          trend="+18%"
          note="77% via WhatsApp"
          bars={[42, 56, 48, 72, 65, 88]}
        />
        <Metric
          label="Healthy syncs"
          value="95.8%"
          trend="+1.2%"
          note="1 tenant needs attention"
          bars={[92, 90, 94, 93, 97, 96]}
        />
        <Metric
          label="Median response"
          value="3.8s"
          trend="-0.4s"
          note="P95 is 9.7 seconds"
          bars={[75, 68, 62, 58, 51, 46]}
        />
      </div>
      <div className="grid grid-2" style={{ marginTop: 18 }}>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Message volume</h2>
              <p>Across WhatsApp and web · last 12 weeks</p>
            </div>
            <select className="filter-select" aria-label="Chart period">
              <option>12 weeks</option>
              <option>30 days</option>
            </select>
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
          />
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <h2>System attention</h2>
              <p>Prioritised operational issues.</p>
            </div>
            <Link className="table-action" href="/admin/errors">
              View all
            </Link>
          </div>
          <div className="timeline">
            <div className="timeline-item">
              <strong>Naya Skin · Bumpa authentication</strong>
              <p>Three sync failures. Last success was 3 days ago.</p>
              <Badge tone="danger">High priority</Badge>
            </div>
            <div className="timeline-item">
              <strong>Bean There Coffee · Partial sync</strong>
              <p>Three profit datasets returned unavailable.</p>
              <Badge tone="warning">Review</Badge>
            </div>
            <div className="timeline-item">
              <strong>Backups completed</strong>
              <p>Postgres and Hermes volumes verified at 02:10.</p>
              <Badge tone="success">Healthy</Badge>
            </div>
          </div>
        </Card>
      </div>
      <Card padded className="">
        <div className="card-head">
          <div>
            <h2>Tenant health</h2>
            <p>Most recently active workspaces.</p>
          </div>
          <Link className="table-action" href="/admin/tenants">
            All tenants →
          </Link>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Tenant</th>
                <th>Status</th>
                <th>Sync</th>
                <th>Health</th>
                <th>Consent</th>
              </tr>
            </thead>
            <tbody>
              {tenants.slice(0, 4).map((t) => (
                <tr key={t.id}>
                  <td>
                    <Link
                      className="table-primary"
                      href={`/admin/tenants/${t.id}`}
                    >
                      {t.name}
                    </Link>
                    <div className="table-secondary">
                      {t.owner} · {t.city}
                    </div>
                  </td>
                  <td>
                    <Badge>{t.status}</Badge>
                  </td>
                  <td>{t.sync}</td>
                  <td>
                    <Badge>{t.health}</Badge>
                  </td>
                  <td>
                    <Badge>{t.consent}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </AppShell>
  );
}

export function TenantList() {
  const [search, setSearch] = useState("");
  const [state, setState] = useState<DemoState>("ready");
  const filtered = useMemo(
    () =>
      tenants.filter((t) =>
        `${t.name} ${t.owner} ${t.city}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [search],
  );
  return (
    <AppShell surface="admin" title="Tenants">
      <PageHeader
        title="SME tenants"
        description="Onboard, monitor, and safely manage every business workspace."
        actions={
          <>
            <DemoStateToggle state={state} setState={setState} />
            <Link className="button button-primary" href="/admin/onboarding">
              ＋ Onboard SME
            </Link>
          </>
        }
      />
      {state !== "ready" ? (
        <States state={state} emptyTitle="No SMEs onboarded" />
      ) : (
        <>
          <Filters search={search} setSearch={setSearch}>
            <select className="filter-select">
              <option>All statuses</option>
              <option>Active</option>
              <option>Onboarding</option>
              <option>Suspended</option>
            </select>
            <select className="filter-select">
              <option>All health</option>
              <option>Healthy</option>
              <option>Attention</option>
            </select>
          </Filters>
          <section className="card table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Business</th>
                  <th>Category</th>
                  <th>Status</th>
                  <th>Users</th>
                  <th>Last sync</th>
                  <th>Health</th>
                  <th>Consent</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <div className="table-primary">{t.name}</div>
                      <div className="table-secondary">
                        {t.owner} · {t.city}
                      </div>
                    </td>
                    <td>{t.category}</td>
                    <td>
                      <Badge>{t.status}</Badge>
                    </td>
                    <td>{t.users}</td>
                    <td>{t.sync}</td>
                    <td>
                      <Badge>{t.health}</Badge>
                    </td>
                    <td>
                      <Badge>{t.consent}</Badge>
                    </td>
                    <td>
                      <Link
                        className="table-action"
                        href={`/admin/tenants/${t.id}`}
                      >
                        Open →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </AppShell>
  );
}

export function TenantDetail({ id }: { id: string }) {
  const tenant = tenants.find((t) => t.id === id) ?? tenants[0];
  const [tab, setTab] = useState("Overview");
  const [toast, setToast] = useState("");
  return (
    <AppShell surface="admin" title={tenant.name}>
      <PageHeader
        title={tenant.name}
        description={`${tenant.category} · ${tenant.city} · Owner: ${tenant.owner}`}
        actions={
          <>
            <button
              className="button button-secondary"
              onClick={() => setToast("Manual sync queued.")}
            >
              ↻ Trigger sync
            </button>
            <button
              className="button button-danger"
              onClick={() =>
                setToast(
                  "Suspension requires typed confirmation in production.",
                )
              }
            >
              Suspend tenant
            </button>
          </>
        }
      />
      <div className="alert alert-success">
        ✓ Core setup is complete. Latest data synced {tenant.sync}; Hermes
        profile is responding.
      </div>
      <div className="tabs" role="tablist">
        {["Overview", "People & phones", "Bumpa", "Hermes", "Audit log"].map(
          (t) => (
            <button
              role="tab"
              aria-selected={tab === t}
              className={`tab ${tab === t ? "active" : ""}`}
              key={t}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ),
        )}
      </div>
      {tab === "Overview" && (
        <div className="grid grid-2">
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Tenant details</h2>
                <p>Identity and research status.</p>
              </div>
              <Badge>{tenant.status}</Badge>
            </div>
            <div className="detail-list">
              {[
                ["Tenant ID", `ten_${tenant.id}_7f4a`],
                ["Timezone", "Africa/Lagos"],
                ["Currency", "NGN"],
                ["Research consent", tenant.consent],
                ["Created", "19 May 2026 by Nneka"],
              ].map(([l, v]) => (
                <div className="detail-row" key={l}>
                  <span className="detail-label">{l}</span>
                  <span className="detail-value">{v}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card padded>
            <div className="card-head">
              <div>
                <h2>Readiness</h2>
                <p>Requirements for an end-to-end conversation.</p>
              </div>
            </div>
            {[
              ["Tenant and owner", 100],
              ["Approved phone", 100],
              ["Bumpa connection", 100],
              ["First successful sync", 100],
              ["Hermes profile", 100],
              ["Research consent", tenant.consent === "Granted" ? 100 : 30],
            ].map(([name, val]) => (
              <div key={String(name)} style={{ marginBottom: 17 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 13,
                    marginBottom: 7,
                  }}
                >
                  <strong>{name}</strong>
                  <span>{val === 100 ? "Complete" : "Pending"}</span>
                </div>
                <div className="progress">
                  <span style={{ width: `${val}%` }} />
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}
      {tab === "People & phones" && (
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Users and approved identities</h2>
              <p>Phone values are masked by default.</p>
            </div>
            <button className="button button-primary button-small">
              ＋ Add user
            </button>
          </div>
          <div className="detail-row">
            <span className="detail-value">Amara Okafor · Owner</span>
            <span>
              <Badge>Approved</Badge> &nbsp; +234 803 ••• 1442
            </span>
          </div>
          <div className="detail-row">
            <span className="detail-value">Tobi Adeyemi · Admin</span>
            <span>
              <Badge>Approved</Badge> &nbsp; +234 706 ••• 0901
            </span>
          </div>
        </Card>
      )}
      {tab === "Bumpa" && (
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Bumpa connection</h2>
              <p>Credentials are write-only and encrypted.</p>
            </div>
            <Badge>Connected</Badge>
          </div>
          <div className="detail-row">
            <span className="detail-label">Scope</span>
            <span className="detail-value">business_id · •••• 7K2A</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Latest run</span>
            <span className="detail-value">Success · 11/11 datasets · 31s</span>
          </div>
          <button className="button button-secondary" style={{ marginTop: 16 }}>
            Replace API key
          </button>
        </Card>
      )}
      {tab === "Hermes" && (
        <Card padded>
          <div className="card-head">
            <div>
              <h2>Hermes profile</h2>
              <p>Private on the internal Docker network.</p>
            </div>
            <Badge>Healthy</Badge>
          </div>
          <div className="detail-row">
            <span className="detail-label">Profile</span>
            <span className="detail-value">tenant_{tenant.id}_7f4a</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Internal port</span>
            <span className="detail-value">8724</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Last response</span>
            <span className="detail-value">3 minutes ago · 2.7s</span>
          </div>
          <button className="button button-secondary" style={{ marginTop: 16 }}>
            Restart profile
          </button>
        </Card>
      )}
      {tab === "Audit log" && (
        <Card padded>
          <div className="timeline">
            <div className="timeline-item">
              <strong>tenant.sync.triggered</strong>
              <p>Nneka · 12 Jul, 10:30 · Manual action</p>
            </div>
            <div className="timeline-item">
              <strong>tenant.phone.approved</strong>
              <p>Nneka · 11 Jul, 14:12 · +234 706 ••• 0901</p>
            </div>
            <div className="timeline-item">
              <strong>research.consent.granted</strong>
              <p>Amara · 9 Jul, 09:18 · Self-service</p>
            </div>
          </div>
        </Card>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

export function UserList() {
  const [search, setSearch] = useState("");
  const users = [
    {
      name: "Amara Okafor",
      tenant: "Kaia Home",
      role: "Owner",
      phone: "+234 803 ••• 1442",
      status: "Active",
      seen: "Now",
    },
    {
      name: "Dami Ajayi",
      tenant: "Morenike Studio",
      role: "Owner",
      phone: "+234 802 ••• 1008",
      status: "Active",
      seen: "41 min ago",
    },
    {
      name: "Feyi Cole",
      tenant: "Bean There Coffee",
      role: "Owner",
      phone: "+234 709 ••• 4512",
      status: "Active",
      seen: "4 hours ago",
    },
    {
      name: "Ife Nwosu",
      tenant: "Naya Skin",
      role: "Owner",
      phone: "+234 813 ••• 0700",
      status: "Revoked",
      seen: "3 days ago",
    },
  ].filter((u) => u.name.toLowerCase().includes(search.toLowerCase()));
  return (
    <AppShell surface="admin" title="Users">
      <PageHeader
        title="Platform users"
        description="Review membership, identity status, and recent activity across tenants."
        actions={<button className="button button-primary">＋ Add user</button>}
      />
      <Filters search={search} setSearch={setSearch} />
      <section className="card table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Tenant</th>
              <th>Role</th>
              <th>Phone</th>
              <th>Status</th>
              <th>Last active</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.name}>
                <td className="table-primary">{u.name}</td>
                <td>{u.tenant}</td>
                <td>{u.role}</td>
                <td>{u.phone}</td>
                <td>
                  <Badge>{u.status}</Badge>
                </td>
                <td>{u.seen}</td>
                <td>
                  <button className="button button-ghost button-small">
                    Manage
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

export function SyncList() {
  const [state, setState] = useState<DemoState>("ready");
  return (
    <AppShell surface="admin" title="Sync runs">
      <PageHeader
        title="Bumpa sync runs"
        description="Monitor freshness, availability, pagination, and upstream failures."
        actions={
          <>
            <DemoStateToggle state={state} setState={setState} />
            <button className="button button-primary">↻ Trigger sync</button>
          </>
        }
      />
      {state !== "ready" ? (
        <States state={state} emptyTitle="No sync runs yet" />
      ) : (
        <>
          <div className="grid grid-4">
            <Metric
              label="Success rate"
              value="95.8%"
              trend="+1.2%"
              note="Last 7 days"
            />
            <Metric
              label="Running now"
              value="2"
              note="Both within threshold"
            />
            <Metric
              label="Partial runs"
              value="4"
              note="Mostly unavailable profit data"
            />
            <Metric
              label="Failed runs"
              value="1"
              note="Authentication rejected"
            />
          </div>
          <section className="card table-wrap" style={{ marginTop: 18 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Tenant</th>
                  <th>Date range</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Datasets</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {syncRuns.map((r) => (
                  <tr key={`${r.tenant}-${r.started}`}>
                    <td className="table-primary">{r.tenant}</td>
                    <td>{r.range}</td>
                    <td>{r.started}</td>
                    <td>{r.duration}</td>
                    <td>{r.datasets}</td>
                    <td>
                      <Badge>{r.status}</Badge>
                    </td>
                    <td>
                      <button className="button button-ghost button-small">
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </AppShell>
  );
}

export function ErrorList() {
  const [search, setSearch] = useState("");
  return (
    <AppShell surface="admin" title="System errors">
      <PageHeader
        title="System errors"
        description="Triage redacted operational errors without exposing secrets or customer PII."
      />
      <div className="alert alert-warning">
        Error metadata is scrubbed before display. Use break-glass access only
        when redacted context is insufficient.
      </div>
      <Filters search={search} setSearch={setSearch}>
        <select className="filter-select">
          <option>Open & investigating</option>
          <option>All statuses</option>
          <option>Resolved</option>
        </select>
      </Filters>
      <div className="grid">
        {errors
          .filter((e) =>
            `${e.service} ${e.tenant} ${e.message}`
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((e) => (
            <Card padded key={e.message}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 16,
                  alignItems: "flex-start",
                }}
              >
                <div>
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      alignItems: "center",
                      marginBottom: 10,
                    }}
                  >
                    <Badge tone={statusTone(e.severity)}>{e.severity}</Badge>
                    <span className="tag">{e.service}</span>
                    <span className="tag">{e.tenant}</span>
                  </div>
                  <strong>{e.message}</strong>
                  <p className="table-secondary">
                    Last occurred {e.happened} · {e.count} occurrence
                    {e.count > 1 ? "s" : ""} · correlation id req_•••7a9
                  </p>
                </div>
                <div>
                  <Badge>{e.status}</Badge>
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
                <button className="button button-secondary button-small">
                  Inspect
                </button>
                <button className="button button-ghost button-small">
                  Mark resolved
                </button>
              </div>
            </Card>
          ))}
      </div>
    </AppShell>
  );
}

export function UsageList() {
  return (
    <AppShell surface="admin" title="Usage">
      <PageHeader
        title="Usage and capacity"
        description="Understand activity, channel mix, and indicative model cost by tenant."
        actions={
          <button className="button button-secondary">⇩ Export CSV</button>
        }
      />
      <div className="grid grid-4">
        <Metric
          label="Total messages"
          value="3,842"
          trend="+18%"
          note="Last 7 days"
        />
        <Metric
          label="Active users"
          value="61"
          trend="+8"
          note="Across 24 SMEs"
        />
        <Metric
          label="WhatsApp share"
          value="77%"
          trend="+4%"
          note="2,958 messages"
        />
        <Metric
          label="Indicative LLM cost"
          value="₦112k"
          trend="-6%"
          note="Within monthly budget"
        />
      </div>
      <section className="card table-wrap" style={{ marginTop: 18 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Tenant</th>
              <th>Messages</th>
              <th>WhatsApp</th>
              <th>Web</th>
              <th>Indicative cost</th>
              <th>Active users</th>
            </tr>
          </thead>
          <tbody>
            {usageRows.map((u) => (
              <tr key={u.tenant}>
                <td className="table-primary">{u.tenant}</td>
                <td>{u.messages.toLocaleString()}</td>
                <td>{u.whatsapp}</td>
                <td>{u.web}</td>
                <td>{u.llm}</td>
                <td>{u.active}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </AppShell>
  );
}

export function Onboarding() {
  const [step, setStep] = useState(0);
  const [toast, setToast] = useState("");
  const steps = ["Business", "Owner", "WhatsApp", "Bumpa", "Hermes", "Review"];
  return (
    <AppShell surface="admin" title="Onboard SME">
      <PageHeader
        title="Onboard a new SME"
        description="Create one isolated tenant and verify every dependency before access begins."
        actions={
          <Link className="button button-secondary" href="/admin/tenants">
            Save and exit
          </Link>
        }
      />
      <div
        className="grid"
        style={{ gridTemplateColumns: "minmax(190px,.35fr) minmax(0,1fr)" }}
      >
        <Card padded>
          <div className="timeline">
            {steps.map((s, i) => (
              <div className="timeline-item" key={s}>
                <strong
                  style={{ color: i === step ? "var(--forest)" : undefined }}
                >
                  {s}
                </strong>
                <p>
                  {i < step
                    ? "Complete"
                    : i === step
                      ? "In progress"
                      : "Not started"}
                </p>
              </div>
            ))}
          </div>
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <span className="eyebrow">
                Step {step + 1} of {steps.length}
              </span>
              <h2 style={{ fontSize: 24, marginTop: 12 }}>{steps[step]}</h2>
            </div>
            <Badge tone={step === steps.length - 1 ? "success" : "warning"}>
              {step === steps.length - 1 ? "Ready" : "Draft"}
            </Badge>
          </div>
          {step === 0 && (
            <>
              <div className="grid grid-2">
                <div className="field">
                  <label>Business name</label>
                  <input className="input" defaultValue="Ori Crafts" />
                </div>
                <div className="field">
                  <label>Slug</label>
                  <input className="input" defaultValue="ori-crafts" />
                </div>
                <div className="field">
                  <label>Category</label>
                  <select className="select">
                    <option>Arts & crafts</option>
                    <option>Fashion</option>
                    <option>Food & drink</option>
                  </select>
                </div>
                <div className="field">
                  <label>City</label>
                  <input className="input" defaultValue="Ibadan" />
                </div>
                <div className="field">
                  <label>Timezone</label>
                  <select className="select">
                    <option>Africa/Lagos</option>
                  </select>
                </div>
                <div className="field">
                  <label>Currency</label>
                  <select className="select">
                    <option>NGN — Nigerian naira</option>
                  </select>
                </div>
              </div>
            </>
          )}
          {step === 1 && (
            <>
              <div className="field">
                <label>Owner full name</label>
                <input className="input" defaultValue="Lola Akanbi" />
              </div>
              <div className="field">
                <label>Owner WhatsApp number</label>
                <input className="input" defaultValue="+234 805 000 2290" />
              </div>
              <div className="field">
                <label>Email (optional)</label>
                <input
                  className="input"
                  type="email"
                  placeholder="lola@example.com"
                />
              </div>
            </>
          )}
          {step === 2 && (
            <>
              <div className="alert alert-info">
                The owner number will become the first approved identity after
                verification.
              </div>
              <div className="detail-row">
                <span className="detail-label">Number</span>
                <span className="detail-value">+234 805 ••• 2290</span>
              </div>
              <button className="button button-secondary">
                Send verification
              </button>
            </>
          )}
          {step === 3 && (
            <>
              <div className="alert alert-warning">
                The API key is write-only. It will be encrypted and cannot be
                viewed after saving.
              </div>
              <div className="field">
                <label>Bumpa API key</label>
                <input
                  className="input"
                  type="password"
                  placeholder="Paste API key"
                />
              </div>
              <div className="field">
                <label>Scope type</label>
                <select className="select">
                  <option>business_id</option>
                  <option>location_id</option>
                </select>
              </div>
              <div className="field">
                <label>Scope ID</label>
                <input className="input" placeholder="Exact scope identifier" />
              </div>
              <button
                className="button button-secondary"
                style={{ marginTop: 16 }}
              >
                Test connection
              </button>
            </>
          )}
          {step === 4 && (
            <>
              <div className="alert alert-success">
                ✓ Profile name and private port can be generated safely.
              </div>
              <div className="detail-row">
                <span className="detail-label">Profile name</span>
                <span className="detail-value">tenant_ori_crafts_91ca</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Proposed internal port</span>
                <span className="detail-value">8729</span>
              </div>
              <button className="button button-secondary">
                Create profile
              </button>
            </>
          )}
          {step === 5 && (
            <div>
              <div className="alert alert-success">
                All technical readiness checks pass. Research consent remains
                pending and will be shown to the owner.
              </div>
              {steps.slice(0, -1).map((s) => (
                <div className="detail-row" key={s}>
                  <span className="detail-value">{s}</span>
                  <Badge>Complete</Badge>
                </div>
              ))}
            </div>
          )}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 10,
              marginTop: 28,
            }}
          >
            <button
              className="button button-secondary"
              disabled={step === 0}
              onClick={() => setStep((v) => v - 1)}
            >
              ← Back
            </button>
            {step < steps.length - 1 ? (
              <button
                className="button button-primary"
                onClick={() => setStep((v) => v + 1)}
              >
                Save and continue →
              </button>
            ) : (
              <button
                className="button button-primary"
                onClick={() =>
                  setToast(
                    "Ori Crafts is ready. Invite delivery will activate with WhatsApp.",
                  )
                }
              >
                Finish onboarding
              </button>
            )}
          </div>
        </Card>
      </div>
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
