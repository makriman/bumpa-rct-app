export type Tenant = {
  id: string;
  slug: string;
  name: string;
  status: string;
  business_category: string | null;
  country: string | null;
  city: string | null;
  timezone: string;
  currency_code: string;
  research_consent_status: string;
  created_at?: string;
};

export type TeamMember = {
  membership_id: string;
  user_id: string;
  name: string;
  email: string | null;
  phone_e164: string;
  role: string;
  status: string;
};

export type PlatformAdmin = {
  user_id: string;
  name: string | null;
  phone_e164: string;
  status: string;
  platform_roles: Array<"operator" | "superadmin">;
  created_at: string;
};

export type WhatsAppNumber = {
  id: string;
  user_id: string;
  phone_e164: string;
  label: string | null;
  status: string;
  opt_out: boolean;
};

export type BumpaStatus = {
  status: string;
  scope_type?: string;
  scope_id_last4?: string;
  provider?: string;
  last_successful_sync_at?: string | null;
  last_error?: string | null;
};

export type SyncRun = {
  id: string;
  tenant_id?: string;
  status: string;
  completion_quality?:
    | "legacy"
    | "pending"
    | "complete"
    | "accepted_partial"
    | "degraded"
    | "failed";
  partial_reason?:
    | "profit_not_calculable"
    | "dataset_unavailable"
    | "dataset_error"
    | "orders_unavailable"
    | "incomplete_dataset_set"
    | null;
  requested_from?: string;
  requested_to?: string;
  dataset_results?: Record<string, unknown> | null;
  started_at: string;
  finished_at?: string | null;
  error?: string | null;
};

export type SystemError = {
  id: string;
  service: string;
  severity: string;
  message: string;
  created_at: string;
};

export type AsyncJob = {
  id: string;
  tenant_id: string | null;
  kind: string;
  status:
    | "pending"
    | "queued"
    | "running"
    | "retry"
    | "succeeded"
    | "dead_letter"
    | "cancelled";
  attempts: number;
  max_attempts: number;
  failure_category: string | null;
  replayable: boolean;
  available_at: string;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AsyncJobReplayReason =
  | "configuration_corrected"
  | "dependency_recovered"
  | "operator_verified_safe_retry"
  | "transient_provider_recovered"
  | "upstream_credentials_rotated";

export type UsageEvent = {
  id: string;
  tenant_id: string | null;
  event_name: string;
  created_at: string;
};

export type AuditEvent = {
  id: string;
  tenant_id: string | null;
  action: string;
  resource_type: string;
  created_at: string;
};

export type ResearchOverviewData = {
  smes_onboarded: number;
  research_events: number;
  messages_by_channel: Record<string, number>;
  questions_by_intent: Record<string, number>;
  bumpa_data_usage: Record<string, number>;
};

export type ResearchEvent = {
  id: string;
  tenant_pseudonym: string;
  channel: string;
  event_type: string;
  redacted_text: string | null;
  primary_intent: string | null;
  business_function: string | null;
  ai_help_type: string | null;
  complexity: string | null;
  bumpa_data_used: string | null;
  created_at: string;
};

export type ResearchConversationSummary = {
  id: string;
  tenant_pseudonym: string | null;
  participant_pseudonyms: string[];
  channel: string;
  event_count: number;
  primary_intents: Record<string, number>;
  latest_redacted_text: string | null;
  started_at: string;
  last_activity_at: string;
};

export type ResearchConversationEvent = Omit<
  ResearchEvent,
  "tenant_pseudonym"
> & {
  user_pseudonym: string | null;
};

export type ResearchConversationDetail = ResearchConversationSummary & {
  events: ResearchConversationEvent[];
};

export type Taxonomy = {
  primary_intent: string[];
  business_function: string[];
  ai_help_type: string[];
  complexity: string[];
};

export type Report = {
  id: string;
  report_type: string;
  status: string;
  title: string | null;
  summary: string | null;
  created_at: string;
  finished_at: string | null;
};

export type McpConnection = {
  id: string;
  provider: string;
  status: string;
  scopes: string[];
  read_only: boolean;
  admin_approved: boolean;
};

export type McpRegistryItem = {
  provider: string;
  name: string;
  enabled: boolean;
};

export function titleCase(value: string | null | undefined): string {
  if (!value) return "Not available";
  return value
    .replaceAll("_", " ")
    .replaceAll(".", " · ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Lagos",
  }).format(date);
}

export function durationBetween(start: string, finish?: string | null): string {
  if (!finish) return "In progress";
  const milliseconds = new Date(finish).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "Unavailable";
  const seconds = Math.round(milliseconds / 1000);
  return seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function maskPhone(value: string): string {
  if (value.length < 7) return value;
  return `${value.slice(0, 6)} ••• ${value.slice(-4)}`;
}

export function countValues<T>(
  values: T[],
  key: (value: T) => string | null | undefined,
): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const item of values) {
    const label = key(item) || "unclassified";
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}
