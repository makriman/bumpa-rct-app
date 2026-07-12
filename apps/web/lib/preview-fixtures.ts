import type {
  AuditEvent,
  BumpaStatus,
  McpConnection,
  McpRegistryItem,
  Report,
  ResearchConversationDetail,
  ResearchConversationSummary,
  ResearchEvent,
  ResearchOverviewData,
  SyncRun,
  SystemError,
  Taxonomy,
  TeamMember,
  Tenant,
  UsageEvent,
  WhatsAppNumber,
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

export const previewTeam: TeamMember[] = [
  {
    membership_id: "demo-membership-owner",
    user_id: "demo-user-owner",
    name: "Amara Okafor",
    email: "amara@example.test",
    phone_e164: "+2348030001442",
    role: "owner",
    status: "active",
  },
  {
    membership_id: "demo-membership-admin",
    user_id: "demo-user-admin",
    name: "Tobi Adeyemi",
    email: "tobi@example.test",
    phone_e164: "+2347060000901",
    role: "admin",
    status: "active",
  },
];

export const previewWhatsAppNumbers: WhatsAppNumber[] = [
  {
    id: "demo-phone-owner",
    user_id: "demo-user-owner",
    phone_e164: "+2348030001442",
    label: "Amara · Owner",
    status: "approved",
    opt_out: false,
  },
  {
    id: "demo-phone-admin",
    user_id: "demo-user-admin",
    phone_e164: "+2347060000901",
    label: "Tobi · Operations",
    status: "approved",
    opt_out: false,
  },
];

export const previewBumpaStatus: BumpaStatus = {
  status: "active",
  scope_type: "business_id",
  scope_id_last4: "7K2A",
  provider: "local",
  last_successful_sync_at: "2026-07-12T09:30:00Z",
  last_error: null,
};

export const previewSyncRuns: SyncRun[] = [
  {
    id: "demo-sync-success",
    tenant_id: "demo-kaia-home",
    status: "success",
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
    requested_from: "2026-07-05",
    requested_to: "2026-07-12",
    dataset_results: { orders: "available", gross_profit: "unavailable" },
    started_at: "2026-07-12T07:18:00Z",
    finished_at: "2026-07-12T07:19:12Z",
    error: "Gross profit was unavailable upstream",
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

export const previewResearchEvents: ResearchEvent[] = [
  {
    id: "demo-event-1",
    tenant_pseudonym: "SME-K4H2",
    channel: "whatsapp",
    event_type: "question",
    redacted_text: "Which products sold best this week?",
    primary_intent: "sales_analysis",
    business_function: "sales",
    ai_help_type: "data_lookup",
    complexity: "simple_lookup",
    bumpa_data_used: "products",
    created_at: "2026-07-12T09:42:00Z",
  },
  {
    id: "demo-event-2",
    tenant_pseudonym: "SME-M8P1",
    channel: "web",
    event_type: "question",
    redacted_text: "Why did revenue fall after payday?",
    primary_intent: "finance",
    business_function: "finance",
    ai_help_type: "diagnosis",
    complexity: "multi_step_reasoning",
    bumpa_data_used: "orders,analytics",
    created_at: "2026-07-12T08:18:00Z",
  },
  {
    id: "demo-event-3",
    tenant_pseudonym: "SME-B2N7",
    channel: "whatsapp",
    event_type: "question",
    redacted_text: "What should I restock before the weekend?",
    primary_intent: "inventory_management",
    business_function: "stock",
    ai_help_type: "recommendation",
    complexity: "single_step_reasoning",
    bumpa_data_used: "products,orders",
    created_at: "2026-07-11T13:51:00Z",
  },
];

export const previewResearchConversations: ResearchConversationSummary[] = [
  {
    id: "CONV-DEMO-7A2F",
    tenant_pseudonym: "SME-K4H2",
    participant_pseudonyms: ["USR-DEMO-31C8"],
    channel: "whatsapp",
    event_count: 2,
    primary_intents: { sales_analysis: 1, inventory_management: 1 },
    latest_redacted_text: "What should I restock before the weekend?",
    started_at: "2026-07-12T09:20:00Z",
    last_activity_at: "2026-07-12T09:42:00Z",
  },
];

export const previewResearchConversationDetails: Record<
  string,
  ResearchConversationDetail
> = {
  "CONV-DEMO-7A2F": {
    ...previewResearchConversations[0],
    events: [
      {
        id: "EVT-DEMO-1A2B",
        user_pseudonym: "USR-DEMO-31C8",
        channel: "whatsapp",
        event_type: "question",
        redacted_text: "Which products sold best this week?",
        primary_intent: "sales_analysis",
        business_function: "sales",
        ai_help_type: "data_lookup",
        complexity: "simple_lookup",
        bumpa_data_used: "products",
        created_at: "2026-07-12T09:20:00Z",
      },
      {
        id: "EVT-DEMO-2C4D",
        user_pseudonym: "USR-DEMO-31C8",
        channel: "whatsapp",
        event_type: "question",
        redacted_text: "What should I restock before the weekend?",
        primary_intent: "inventory_management",
        business_function: "stock",
        ai_help_type: "recommendation",
        complexity: "single_step_reasoning",
        bumpa_data_used: "products,orders",
        created_at: "2026-07-12T09:42:00Z",
      },
    ],
  },
};

export const previewResearchOverview: ResearchOverviewData = {
  smes_onboarded: 3,
  research_events: 3,
  messages_by_channel: { whatsapp: 2, web: 1 },
  questions_by_intent: {
    sales_analysis: 1,
    finance: 1,
    inventory_management: 1,
  },
  bumpa_data_usage: { products: 2, orders: 2, analytics: 1 },
};

export const previewTaxonomy: Taxonomy = {
  primary_intent: [
    "sales_analysis",
    "inventory_management",
    "customer_management",
    "marketing",
    "finance",
    "operations",
    "order_management",
    "product_strategy",
    "platform_support",
    "general_business_advice",
    "other",
  ],
  business_function: [
    "sales",
    "stock",
    "customers",
    "ads",
    "finance",
    "fulfillment",
    "staff",
    "strategy",
    "admin",
  ],
  ai_help_type: [
    "data_lookup",
    "explanation",
    "diagnosis",
    "recommendation",
    "forecast",
    "report",
    "draft_message",
    "teaching",
    "troubleshooting",
  ],
  complexity: [
    "simple_lookup",
    "single_step_reasoning",
    "multi_step_reasoning",
    "strategic_reasoning",
  ],
};

export const previewReports: Report[] = [
  {
    id: "demo-report-weekly",
    report_type: "weekly_memo",
    status: "ready",
    title: "Weekly research memo · W28",
    summary: "A redacted fixture report for local interface review.",
    created_at: "2026-07-12T10:04:00Z",
    finished_at: "2026-07-12T10:04:02Z",
  },
];

export const previewMcpConnections: McpConnection[] = [];

export const previewMcpRegistry: McpRegistryItem[] = [
  { provider: "google_drive", name: "Google Drive", enabled: false },
  { provider: "google_sheets", name: "Google Sheets", enabled: false },
  { provider: "gmail", name: "Gmail", enabled: false },
  { provider: "calendar", name: "Google Calendar", enabled: false },
  { provider: "meta_ads", name: "Meta Ads", enabled: false },
];
