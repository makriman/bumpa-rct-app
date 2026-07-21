import type { components } from "@bumpabestie/web-foundation";

export {
  countValues,
  durationBetween,
  formatLagosDate as formatDate,
  maskPhone,
  titleCase,
} from "@bumpabestie/web-foundation";

type Schemas = components["schemas"];

export type AdminExport = Schemas["AdminExportView"];
export type AsyncJob = Schemas["AsyncJobView"];
export type AsyncJobReplayReason = Schemas["AsyncJobReplayRequest"]["reason"];
export type HermesCallError = Schemas["HermesCallErrorView"];
export type McpAdminConnection = Schemas["McpAdminConnectionView"];
export type PlatformAccess = Schemas["PlatformAccessView"];
export type TenantOnboarding = Schemas["OnboardingView"];
export type OnboardingStep = TenantOnboarding["current_step"];
export type TenantOperations = Schemas["TenantOperationsView"];
export type WhatsAppDeliveryFailure = Schemas["WhatsappDeliveryFailureView"];

// These endpoints intentionally expose bounded operational projections that
// are not yet named OpenAPI component schemas. Keep their local types narrow
// until the backend contract publishes generated models for them.
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
