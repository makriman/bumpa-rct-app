"use client";

import { useState } from "react";
import {
  parseApiErrorPayload,
  responseError,
} from "@bumpabestie/web-foundation";
import { AppIcon } from "@/components/app-icon";
import { apiRequest } from "@/lib/api";
import {
  formatDate,
  titleCase,
  type Report,
  type Taxonomy,
} from "@/lib/platform-data";
import { useApiResource } from "@/lib/use-api-resource";
import { AppShell } from "./app-shell";
import { LiveDataBanner } from "./live-data-banner";
import {
  Badge,
  Modal,
  PageHeader,
  ScrollableTable,
  StatePanel,
  Toast,
} from "./ui";

type ArtifactMode = "reports" | "exports";
type ArtifactForm = {
  type: string;
  formats: string[];
  dateFrom: string;
  dateTo: string;
  tenantPseudonym: string;
  channel: string;
  primaryIntent: string;
  businessFunction: string;
  aiHelpType: string;
  complexity: string;
  accessReason: string;
};

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

function initialForm(mode: ArtifactMode): ArtifactForm {
  return {
    type: mode === "reports" ? "weekly_memo" : "sme_usage",
    formats: [mode === "reports" ? "pdf" : "csv"],
    dateFrom: "",
    dateTo: "",
    tenantPseudonym: "",
    channel: "",
    primaryIntent: "",
    businessFunction: "",
    aiHelpType: "",
    complexity: "",
    accessReason: "",
  };
}

function selectedFilters(form: ArtifactForm) {
  return Object.fromEntries(
    Object.entries({
      date_from: form.dateFrom,
      date_to: form.dateTo,
      tenant_pseudonym: form.tenantPseudonym,
      channel: form.channel,
      primary_intent: form.primaryIntent,
      business_function: form.businessFunction,
      ai_help_type: form.aiHelpType,
      complexity: form.complexity,
    }).flatMap(([key, value]) => {
      const trimmed = value.trim();
      return trimmed ? [[key, trimmed]] : [];
    }),
  );
}

function useArtifactInventory(mode: ArtifactMode) {
  const resource = useApiResource<Report[]>(
    `/research/reports?artifact_kind=${mode.slice(0, -1)}`,
  );
  const taxonomy = useApiResource<Taxonomy>("/research/taxonomy");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(() => initialForm(mode));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const noun = mode === "reports" ? "report" : "export";
  const raw = form.type === "raw_export_package";
  const visibleReports = (resource.data ?? []).filter(
    (report) =>
      !report.artifact_kind || report.artifact_kind === mode.slice(0, -1),
  );
  const updateForm = (value: Partial<ArtifactForm>) =>
    setForm((current) => ({ ...current, ...value }));
  const toggleFormat = (candidate: string) =>
    setForm((current) => ({
      ...current,
      formats: current.formats.includes(candidate)
        ? current.formats.filter((item) => item !== candidate)
        : [...current.formats, candidate],
    }));

  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await apiRequest<Report>(
        mode === "reports" ? "/research/reports" : "/research/exports",
        {
          method: "POST",
          headers: raw
            ? { "X-Access-Reason": form.accessReason.trim() }
            : undefined,
          body: JSON.stringify({
            report_type: form.type,
            filters: selectedFilters(form),
            formats: form.formats,
          }),
        },
      );
      resource.replace([created, ...(resource.data ?? [])]);
      updateForm({ accessReason: "" });
      setCreateOpen(false);
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
      updateForm({ accessReason: "" });
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
            ? { "X-Access-Reason": form.accessReason.trim() }
            : undefined,
        },
      );
      if (!response.ok) {
        const payload = parseApiErrorPayload(
          await response.json().catch(() => null),
        );
        throw responseError(response, payload);
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `research-report-${detail.id}.${artifact.format}`;
      anchor.click();
      URL.revokeObjectURL(url);
      updateForm({ accessReason: "" });
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

  return {
    busy,
    create,
    createOpen,
    detail,
    detailBusy,
    download,
    error,
    form,
    inspect,
    mode,
    noun,
    raw,
    resource,
    setCreateOpen,
    setDetail,
    setToast,
    taxonomy,
    toast,
    toggleFormat,
    updateForm,
    visibleReports,
  };
}

type ArtifactView = ReturnType<typeof useArtifactInventory>;

function ReportInventory({ mode }: { mode: ArtifactMode }) {
  const view = useArtifactInventory(mode);
  return (
    <AppShell title={mode === "reports" ? "Reports" : "Exports"}>
      <PageHeader
        title={mode === "reports" ? "Research reports" : "Export centre"}
        description={
          mode === "reports"
            ? "Generate durable reports using the configured artifact adapter."
            : "Create anonymised, audit-logged research artifacts."
        }
        actions={
          <button
            type="button"
            className="button button-primary"
            disabled={view.resource.source !== "live"}
            title={
              view.resource.source !== "live"
                ? "Artifact creation requires a reachable live API."
                : undefined
            }
            onClick={() => view.setCreateOpen(true)}
          >
            <AppIcon name="add" size={16} /> Create {view.noun}
          </button>
        }
      />
      <LiveDataBanner
        label="research artifacts"
        source={view.resource.source}
        status={view.resource.status}
        count={view.resource.data?.length}
        error={view.resource.error}
      />
      {view.error && (
        <div className="alert alert-danger" role="alert">
          {view.error}
        </div>
      )}
      <ArtifactList view={view} />
      {view.createOpen && <CreateArtifactDialog view={view} />}
      {view.detail && <ArtifactDetailDialog view={view} detail={view.detail} />}
      {view.toast && (
        <Toast message={view.toast} onClose={() => view.setToast("")} />
      )}
    </AppShell>
  );
}

function ArtifactList({ view }: { view: ArtifactView }) {
  if (view.resource.status === "loading") return <StatePanel type="loading" />;
  if (view.resource.status === "error") {
    return (
      <StatePanel
        type="error"
        description={view.resource.error ?? undefined}
        action={
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void view.resource.reload()}
          >
            Try again
          </button>
        }
      />
    );
  }
  if (!view.visibleReports.length) {
    return (
      <StatePanel
        type="empty"
        title={`No ${view.noun}s generated`}
        description="Generated artifacts will appear here when they are ready."
      />
    );
  }
  return (
    <ScrollableTable className="card" label={`${view.noun} artifacts`}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Artifact</th>
            <th>Type</th>
            <th>Created</th>
            <th>Finished</th>
            <th>Status</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {view.visibleReports.map((report) => (
            <tr key={report.id}>
              <td>
                <div className="table-primary">
                  {report.title ?? titleCase(report.report_type)}
                </div>
                <div className="table-secondary">{report.id.slice(0, 12)}</div>
              </td>
              <td>{titleCase(report.report_type)}</td>
              <td>{formatDate(report.created_at)}</td>
              <td>{formatDate(report.finished_at)}</td>
              <td>
                <Badge>{titleCase(report.status)}</Badge>
              </td>
              <td>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={view.detailBusy || view.resource.source !== "live"}
                  onClick={() => void view.inspect(report.id)}
                >
                  {view.detailBusy ? "Loading…" : "View files"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollableTable>
  );
}

function CreateArtifactDialog({ view }: { view: ArtifactView }) {
  const form = view.form;
  const invalidDates = Boolean(
    form.dateFrom && form.dateTo && form.dateTo < form.dateFrom,
  );
  return (
    <Modal
      title={`Create research ${view.noun}`}
      onClose={() => !view.busy && view.setCreateOpen(false)}
      actions={
        <>
          <button
            type="button"
            className="button button-secondary"
            disabled={view.busy}
            onClick={() => view.setCreateOpen(false)}
          >
            Cancel
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={
              view.busy ||
              !form.formats.length ||
              (view.raw && form.accessReason.trim().length < 12) ||
              invalidDates
            }
            onClick={() => void view.create()}
          >
            {view.busy ? "Generating…" : `Generate ${view.noun}`}
          </button>
        </>
      }
    >
      <ArtifactTypeField
        mode={view.mode}
        form={form}
        update={view.updateForm}
      />
      <FormatFields form={form} toggle={view.toggleFormat} />
      <FilterFields
        form={form}
        taxonomy={view.taxonomy.data}
        update={view.updateForm}
      />
      {view.taxonomy.status === "error" && (
        <div className="alert alert-warning" role="status">
          Research taxonomy could not be loaded. Optional classification filters
          are unavailable.
          <button
            type="button"
            className="button button-tertiary"
            onClick={() => void view.taxonomy.reload()}
          >
            Try again
          </button>
        </div>
      )}
      {view.raw && (
        <div className="field">
          <label htmlFor="raw-export-reason">Required access reason</label>
          <textarea
            id="raw-export-reason"
            className="textarea"
            minLength={12}
            maxLength={240}
            value={form.accessReason}
            placeholder="Explain the approved research purpose without personal data."
            onChange={(event) =>
              view.updateForm({ accessReason: event.target.value })
            }
          />
          <span className="field-help">
            Raw packages are superadmin-only. Creation and every download are
            separately audited.
          </span>
        </div>
      )}
      <div className="alert alert-info">
        Requests are consent-filtered and audit-logged. In production,
        generation runs on the durable report queue.
      </div>
    </Modal>
  );
}

function ArtifactTypeField({
  mode,
  form,
  update,
}: {
  mode: ArtifactMode;
  form: ArtifactForm;
  update: (value: Partial<ArtifactForm>) => void;
}) {
  return (
    <div className="field">
      <label htmlFor="report-type">Artifact type</label>
      <select
        id="report-type"
        className="select"
        value={form.type}
        onChange={(event) => update({ type: event.target.value })}
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
  );
}

function FormatFields({
  form,
  toggle,
}: {
  form: ArtifactForm;
  toggle: (value: string) => void;
}) {
  return (
    <div className="field">
      <span className="field-label" id="report-formats-label">
        Formats
      </span>
      <div
        aria-labelledby="report-formats-label"
        role="group"
        style={{ display: "flex", flexWrap: "wrap", gap: 10 }}
      >
        {[
          ["csv", "CSV"],
          ["jsonl", "JSONL"],
          ["pdf", "PDF"],
        ].map(([value, label]) => (
          <label className="button button-secondary button-small" key={value}>
            <input
              type="checkbox"
              checked={form.formats.includes(value)}
              onChange={() => toggle(value)}
            />{" "}
            {label}
          </label>
        ))}
      </div>
      {!form.formats.length && (
        <span className="field-error">Select at least one format.</span>
      )}
    </div>
  );
}

function FilterFields({
  form,
  taxonomy,
  update,
}: {
  form: ArtifactForm;
  taxonomy: Taxonomy | null;
  update: (value: Partial<ArtifactForm>) => void;
}) {
  return (
    <>
      <div className="grid grid-2">
        <Field id="report-date-from" label="From date">
          <input
            id="report-date-from"
            className="input"
            type="date"
            value={form.dateFrom}
            onChange={(event) => update({ dateFrom: event.target.value })}
          />
        </Field>
        <Field id="report-date-to" label="To date">
          <input
            id="report-date-to"
            className="input"
            type="date"
            value={form.dateTo}
            min={form.dateFrom || undefined}
            onChange={(event) => update({ dateTo: event.target.value })}
          />
        </Field>
      </div>
      <Field id="tenant-pseudonym" label="SME pseudonym">
        <input
          id="tenant-pseudonym"
          className="input"
          aria-label="SME pseudonym"
          value={form.tenantPseudonym}
          placeholder="Optional exact pseudonym"
          onChange={(event) => update({ tenantPseudonym: event.target.value })}
        />
      </Field>
      <div className="grid grid-2">
        <SelectField
          id="report-channel"
          label="Channel"
          value={form.channel}
          options={[
            ["", "All channels"],
            ["web", "Web"],
            ["whatsapp", "WhatsApp"],
          ]}
          onChange={(channel) => update({ channel })}
        />
        <TaxonomyField
          id="report-intent"
          label="Question category"
          value={form.primaryIntent}
          options={taxonomy?.primary_intent ?? []}
          onChange={(primaryIntent) => update({ primaryIntent })}
        />
        <TaxonomyField
          id="report-function"
          label="Business function"
          value={form.businessFunction}
          options={taxonomy?.business_function ?? []}
          onChange={(businessFunction) => update({ businessFunction })}
        />
        <TaxonomyField
          id="report-help-type"
          label="AI help type"
          value={form.aiHelpType}
          options={taxonomy?.ai_help_type ?? []}
          onChange={(aiHelpType) => update({ aiHelpType })}
        />
        <TaxonomyField
          id="report-complexity"
          label="Complexity"
          value={form.complexity}
          options={taxonomy?.complexity ?? []}
          onChange={(complexity) => update({ complexity })}
        />
      </div>
    </>
  );
}

function Field({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
    </div>
  );
}

function SelectField({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: string[][];
  onChange: (value: string) => void;
}) {
  return (
    <Field id={id} label={label}>
      <select
        id={id}
        className="select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([option, display]) => (
          <option value={option} key={option || "all"}>
            {display}
          </option>
        ))}
      </select>
    </Field>
  );
}

function TaxonomyField({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <SelectField
      id={id}
      label={label}
      value={value}
      options={[
        ["", `All ${label.toLowerCase()}s`],
        ...options.map((item) => [item, titleCase(item)]),
      ]}
      onChange={onChange}
    />
  );
}

function ArtifactDetailDialog({
  view,
  detail,
}: {
  view: ArtifactView;
  detail: ReportDetail;
}) {
  const raw = detail.report_type === "raw_export_package";
  return (
    <Modal
      title={detail.title ?? "Artifact files"}
      onClose={() => view.setDetail(null)}
      actions={
        <button
          type="button"
          className="button button-secondary"
          onClick={() => view.setDetail(null)}
        >
          Close
        </button>
      }
    >
      <p>{detail.summary ?? "No summary was recorded."}</p>
      {raw && (
        <div className="field">
          <label htmlFor="raw-download-reason">Fresh download reason</label>
          <textarea
            id="raw-download-reason"
            className="textarea"
            minLength={12}
            maxLength={240}
            value={view.form.accessReason}
            placeholder="State the approved research purpose for this download."
            onChange={(event) =>
              view.updateForm({ accessReason: event.target.value })
            }
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
              type="button"
              className="button button-primary button-small"
              disabled={
                view.detailBusy ||
                (raw && view.form.accessReason.trim().length < 12)
              }
              onClick={() => void view.download(artifact)}
            >
              <AppIcon name="download" size={16} />{" "}
              {view.detailBusy ? "Checking…" : "Download"}
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
  );
}

export function Reports() {
  return <ReportInventory mode="reports" />;
}

export function Exports() {
  return <ReportInventory mode="exports" />;
}
