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

export type PlatformAccess = {
  user_id: string;
  name: string | null;
  phone_e164: string;
  status: string;
  has_active_mapping: boolean;
  platform_roles: Array<"operator" | "researcher" | "superadmin">;
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
  store_timezone?: string;
  store_currency?: string;
  provider?: string;
  last_successful_sync_at?: string | null;
  last_error?: string | null;
};

/**
 * Authoritative projection for the resumable tenant-provisioning saga. The
 * browser renders this projection after every command; it never reconstructs
 * completion from local form state.
 */
export type OnboardingStep =
  | "owner"
  | "phone"
  | "bumpa"
  | "initial_sync"
  | "hermes"
  | "review"
  | "completed";

export type OnboardingStatus =
  | "in_progress"
  | "attention_required"
  | "completed";

export type TenantOnboarding = {
  id: string;
  tenant_id: string;
  status: OnboardingStatus;
  current_step: OnboardingStep;
  revision: number;
  tenant: {
    id: string;
    slug: string;
    name: string;
    status: string;
    timezone: string;
    currency_code: string;
  };
  owner: {
    user_id: string;
    membership_id: string;
    name: string | null;
    email_masked: string | null;
    status: string;
  } | null;
  phone: {
    identity_id: string;
    label: string | null;
    phone_masked: string;
    status: string;
    opt_out: boolean;
  } | null;
  bumpa: {
    connection_id: string;
    provider: string;
    status: string;
    scope_type: string;
    scope_id_last4: string;
    store_timezone: string;
    store_currency: string;
  } | null;
  initial_sync: {
    attempt: number;
    requested_from: string;
    requested_to: string;
    job_id: string;
    job_status: string;
    sync_run_id: string | null;
    sync_status: string | null;
    completion_quality: string | null;
    orders_availability: string | null;
    orders_count: number | null;
  } | null;
  hermes: {
    profile_id: string;
    profile_name: string;
    provider: string;
    status: string;
    api_port: number | null;
  } | null;
  failure: {
    code: string;
    step: OnboardingStep;
    retryable: boolean;
    at: string;
  } | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type TenantOperations = {
  tenant_id: string;
  people: Array<{
    membership_id: string;
    user_id: string;
    name: string | null;
    phone_masked: string;
    role: string;
    status: string;
  }>;
  phones: Array<{
    id: string;
    user_id: string;
    phone_masked: string;
    label: string | null;
    status: string;
    opt_out: boolean;
  }>;
  bumpa: {
    connected: boolean;
    status: string;
    scope_type: string | null;
    scope_id_last4: string | null;
    store_timezone: string | null;
    store_currency: string | null;
    provider: string | null;
    last_successful_sync_at: string | null;
    last_failed_sync_at: string | null;
    last_error: string | null;
  };
  hermes: {
    provisioned: boolean;
    profile_name: string | null;
    provider: string | null;
    status: string;
    api_port: number | null;
  };
};

export type WhatsAppDeliveryFailure = {
  id: string;
  tenant_id: string | null;
  message_reference: string;
  phone_masked: string | null;
  status: string;
  provider_error_code: string | null;
  provider_error_title: string | null;
  created_at: string;
};

export type HermesCallError = {
  id: string;
  tenant_id: string | null;
  category: string;
  retryable: boolean | null;
  profile_reference: string | null;
  created_at: string;
};

export type AdminExport = {
  export_id: string;
  filename: string;
  content_type: "text/csv";
  content: string;
  row_count: number;
  checksum_sha256: string;
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
    | "optional_dataset_unavailable"
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
  generated_at: string;
  smes_onboarded: number;
  research_consent_status: Record<string, number>;
  research_events: number;
  active_smes: {
    day: number;
    week: number;
    month: number;
  };
  active_users_by_channel: Record<string, number>;
  messages_by_channel: Record<string, number>;
  questions_by_category: Record<string, number>;
  questions_by_intent: Record<string, number>;
  questions_by_business_function: Record<string, number>;
  questions_by_complexity: Record<string, number>;
  questions_by_ai_help_type: Record<string, number>;
  bumpa_data_usage: Record<string, number>;
  hermes_response_latency: {
    samples: number;
    average_ms: number | null;
    p50_ms: number | null;
    p95_ms: number | null;
  };
  bumpa_sync_freshness: {
    connected_smes: number;
    fresh_24h: number;
    stale_24_to_72h: number;
    overdue_72h: number;
    never_synced: number;
    latest_sync_at: string | null;
    oldest_sync_at: string | null;
  };
  report_generation: {
    total: number;
    by_status: Record<string, number>;
    by_type: Record<string, number>;
  };
  exports: {
    total: number;
    by_format: Record<string, number>;
  };
  retention_by_cohort: Array<{
    cohort: string;
    smes: number;
    eligible_7d: number;
    retained_7d: number;
    retention_7d_pct: number | null;
    eligible_30d: number;
    retained_30d: number;
    retention_30d_pct: number | null;
  }>;
  repeat_usage: {
    smes_observed: number;
    repeat_smes: number;
    repeat_rate_pct: number | null;
    by_sme: Array<{
      tenant_pseudonym: string;
      event_count: number;
      active_days: number;
      first_seen_at: string;
      last_seen_at: string;
    }>;
  };
  top_recurring_problems: ResearchRankedItem[];
  most_common_sales_questions: ResearchRankedItem[];
  most_common_inventory_questions: ResearchRankedItem[];
  most_common_customer_questions: ResearchRankedItem[];
  most_common_advice_requests: ResearchRankedItem[];
};

export type ResearchRankedItem = {
  label: string;
  count: number;
};

export type ResearchEvent = {
  id: string;
  tenant_pseudonym: string;
  channel: string;
  event_type: string;
  raw_text_present: boolean;
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
  artifact_kind: "report" | "export";
  status: string;
  title: string | null;
  summary: string | null;
  created_at: string;
  finished_at: string | null;
};

export type McpProvider =
  | "google_drive"
  | "google_sheets"
  | "gmail"
  | "calendar"
  | "meta_ads";

export type McpConnection = {
  id: string;
  provider: McpProvider;
  status: string;
  scopes: string[];
  read_only: boolean;
  admin_approved: boolean;
  oauth_available: boolean;
  permissions: Record<string, McpToolPermission>;
};

export type McpToolPermission = "deny" | "read" | "write_with_confirmation";

export type McpRegistryTool = {
  name: string;
  label: string;
  kind: "read" | "write";
};

export type McpRegistryItem = {
  provider: McpProvider;
  name: string;
  enabled: boolean;
  default_mode: "read_only";
  tools: McpRegistryTool[];
};

export type McpAdminConnection = McpConnection & {
  tenant_id: string;
  tenant_name: string;
  created_by: string | null;
  created_at: string;
};

const LAGOS_DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Africa/Lagos",
});

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
  return LAGOS_DATE_TIME.format(date);
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
