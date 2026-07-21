import type {
  AuditEvent,
  PlatformAccess,
  SyncRun,
  SystemError,
  AsyncJob,
  Tenant,
  UsageEvent,
} from "./platform-data";

export const previewTenants: Tenant[] = [
  {
    id: "demo-kaia-home",
    slug: "kaia-home",
    name: "Kaia Home",
    status: "active",
    business_category: "home_and_living",
    country: "NG",
    city: "Lagos",
    timezone: "Africa/Lagos",
    currency_code: "NGN",
    research_consent_status: "granted",
    created_at: "2026-05-19T09:00:00Z",
  },
  {
    id: "demo-morenike",
    slug: "morenike-studio",
    name: "Morenike Studio",
    status: "active",
    business_category: "fashion",
    country: "NG",
    city: "Abuja",
    timezone: "Africa/Lagos",
    currency_code: "NGN",
    research_consent_status: "granted",
    created_at: "2026-06-02T11:20:00Z",
  },
  {
    id: "demo-bean-there",
    slug: "bean-there-coffee",
    name: "Bean There Coffee",
    status: "active",
    business_category: "food_and_drink",
    country: "NG",
    city: "Lagos",
    timezone: "Africa/Lagos",
    currency_code: "NGN",
    research_consent_status: "pending",
    created_at: "2026-06-11T08:10:00Z",
  },
];

export const previewPlatformAdmins: PlatformAccess[] = [
  {
    user_id: "demo-platform-superadmin",
    name: "Demo superadmin",
    phone_e164: "+2348099990000",
    status: "active",
    has_active_mapping: true,
    platform_roles: ["superadmin", "operator", "researcher"],
    created_at: "2026-05-19T09:00:00Z",
  },
  {
    user_id: "demo-platform-operator",
    name: "Demo operator",
    phone_e164: "+2348099990001",
    status: "active",
    has_active_mapping: false,
    platform_roles: ["operator"],
    created_at: "2026-06-02T11:20:00Z",
  },
  {
    user_id: "demo-platform-researcher",
    name: "Demo researcher",
    phone_e164: "+2348099990002",
    status: "active",
    has_active_mapping: false,
    platform_roles: ["researcher"],
    created_at: "2026-06-03T08:40:00Z",
  },
  {
    user_id: "demo-mapped-collaborator",
    name: "Demo mapped collaborator",
    phone_e164: "+2348030001442",
    status: "active",
    has_active_mapping: true,
    platform_roles: [],
    created_at: "2026-06-04T10:10:00Z",
  },
];

export const previewSyncRuns: SyncRun[] = [
  {
    id: "demo-sync-success",
    tenant_id: "demo-kaia-home",
    status: "success",
    completion_quality: "complete",
    partial_reason: null,
    requested_from: "2026-07-05",
    requested_to: "2026-07-12",
    dataset_results: { orders: "available", products: "available" },
    started_at: "2026-07-12T09:30:00Z",
    finished_at: "2026-07-12T09:30:31Z",
    error: null,
  },
  {
    id: "demo-sync-partial",
    tenant_id: "demo-bean-there",
    status: "partial",
    completion_quality: "accepted_partial",
    partial_reason: "profit_not_calculable",
    requested_from: "2026-07-05",
    requested_to: "2026-07-12",
    dataset_results: { orders: "available", gross_profit: "unavailable" },
    started_at: "2026-07-12T07:18:00Z",
    finished_at: "2026-07-12T07:19:12Z",
    error: null,
  },
];

export const previewErrors: SystemError[] = [
  {
    id: "demo-error-auth",
    service: "bumpa_sync",
    severity: "high",
    message: "Authentication rejected by the upstream API",
    created_at: "2026-07-10T01:00:00Z",
  },
  {
    id: "demo-error-delivery",
    service: "whatsapp",
    severity: "medium",
    message: "Template delivery rejected: recipient unavailable",
    created_at: "2026-07-12T07:42:00Z",
  },
];

export const previewDeadLetterJobs: AsyncJob[] = [
  {
    id: "demo-job-bumpa-sync",
    tenant_id: "demo-kaia-home",
    kind: "bumpa.sync",
    status: "dead_letter",
    attempts: 5,
    max_attempts: 5,
    failure_category: "execution_failure",
    replayable: true,
    available_at: "2026-07-12T07:40:00Z",
    finished_at: "2026-07-12T07:42:00Z",
    created_at: "2026-07-12T07:30:00Z",
    updated_at: "2026-07-12T07:42:00Z",
  },
];

export const previewUsage: UsageEvent[] = [
  {
    id: "demo-usage-1",
    tenant_id: "demo-kaia-home",
    event_name: "chat.web.message",
    created_at: "2026-07-12T09:42:00Z",
  },
  {
    id: "demo-usage-2",
    tenant_id: "demo-kaia-home",
    event_name: "chat.whatsapp.message",
    created_at: "2026-07-12T09:41:00Z",
  },
  {
    id: "demo-usage-3",
    tenant_id: "demo-morenike",
    event_name: "chat.whatsapp.message",
    created_at: "2026-07-12T08:18:00Z",
  },
];

export const previewAudits: AuditEvent[] = [
  {
    id: "demo-audit-1",
    tenant_id: "demo-kaia-home",
    action: "tenant.sync.triggered",
    resource_type: "bumpa_sync_run",
    created_at: "2026-07-12T09:30:00Z",
  },
];
