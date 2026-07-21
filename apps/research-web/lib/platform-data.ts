import type { components } from "@bumpabestie/web-foundation";

export {
  countValues,
  durationBetween,
  formatLagosDate as formatDate,
  maskPhone,
  titleCase,
} from "@bumpabestie/web-foundation";

type Schemas = components["schemas"];

export type Report = Schemas["ReportView"];
export type ResearchConversationSummary =
  Schemas["ResearchConversationSummaryView"];
export type ResearchConversationEvent =
  Schemas["ResearchConversationEventView"];
export type ResearchConversationDetail =
  Schemas["ResearchConversationDetailView"];

// These consent-filtered aggregate endpoints do not yet publish named OpenAPI
// component schemas. Keep their local projections research-only and narrow.
export type ResearchRankedItem = {
  label: string;
  count: number;
};

export type ResearchOverviewData = {
  generated_at: string;
  smes_onboarded: number;
  research_consent_status: Record<string, number>;
  research_events: number;
  active_smes: { day: number; week: number; month: number };
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
  exports: { total: number; by_format: Record<string, number> };
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

export type Taxonomy = {
  primary_intent: string[];
  business_function: string[];
  ai_help_type: string[];
  complexity: string[];
};
