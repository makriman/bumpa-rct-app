import type {
  AuditEvent,
  BumpaStatus,
  McpConnection,
  McpRegistryItem,
  PlatformAdmin,
  Report,
  ResearchConversationDetail,
  ResearchConversationSummary,
  ResearchEvent,
  ResearchOverviewData,
  SyncRun,
  SystemError,
  AsyncJob,
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

export const previewPlatformAdmins: PlatformAdmin[] = [
  {
    user_id: "demo-platform-superadmin",
    name: "Demo superadmin",
    phone_e164: "+2348099990000",
    status: "active",
    platform_roles: ["operator", "superadmin"],
    created_at: "2026-05-19T09:00:00Z",
  },
  {
    user_id: "demo-platform-operator",
    name: "Demo operator",
    phone_e164: "+2348099990001",
    status: "active",
    platform_roles: ["operator"],
    created_at: "2026-06-02T11:20:00Z",
  },
];

export const previewPlatformAdminSession = {
  user: { id: "demo-platform-superadmin" },
  platform_roles: ["operator", "superadmin"],
  memberships: [
    {
      id: "demo-owner-membership",
      tenant_id: "demo-kaia-home",
      role: "owner",
      status: "active",
    },
  ],
  current_tenant_id: "demo-kaia-home",
};

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

export const previewResearchEvents: ResearchEvent[] = [
  {
    id: "demo-event-1",
    tenant_pseudonym: "SME-K4H2",
    channel: "whatsapp",
    event_type: "question",
    raw_text_present: true,
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
    raw_text_present: true,
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
    raw_text_present: true,
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
        raw_text_present: true,
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
        raw_text_present: true,
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
  generated_at: "2026-07-12T10:15:00Z",
  smes_onboarded: 3,
  research_consent_status: { granted: 2, pending: 1 },
  research_events: 3,
  active_smes: { day: 2, week: 2, month: 2 },
  active_users_by_channel: { whatsapp: 2, web: 1 },
  messages_by_channel: { whatsapp: 2, web: 1 },
  questions_by_category: {
    sales_analysis: 1,
    finance: 1,
    inventory_management: 1,
  },
  questions_by_intent: {
    sales_analysis: 1,
    finance: 1,
    inventory_management: 1,
  },
  questions_by_business_function: { sales: 1, finance: 1, stock: 1 },
  questions_by_complexity: {
    simple_lookup: 1,
    single_step_reasoning: 1,
    multi_step_reasoning: 1,
  },
  questions_by_ai_help_type: {
    data_lookup: 1,
    diagnosis: 1,
    recommendation: 1,
  },
  bumpa_data_usage: { products: 2, orders: 2, analytics: 1 },
  hermes_response_latency: {
    samples: 3,
    average_ms: 1280,
    p50_ms: 1170,
    p95_ms: 1840,
  },
  bumpa_sync_freshness: {
    connected_smes: 2,
    fresh_24h: 2,
    stale_24_to_72h: 0,
    overdue_72h: 0,
    never_synced: 0,
    latest_sync_at: "2026-07-12T10:00:00Z",
    oldest_sync_at: "2026-07-12T09:42:00Z",
  },
  report_generation: {
    total: 4,
    by_status: { success: 3, queued: 1 },
    by_type: { weekly_memo: 2, question_taxonomy: 1, sme_usage: 1 },
  },
  exports: { total: 7, by_format: { csv: 3, jsonl: 2, pdf: 2 } },
  retention_by_cohort: [
    {
      cohort: "2026-06",
      smes: 2,
      eligible_7d: 2,
      retained_7d: 2,
      retention_7d_pct: 100,
      eligible_30d: 0,
      retained_30d: 0,
      retention_30d_pct: null,
    },
  ],
  repeat_usage: {
    smes_observed: 2,
    repeat_smes: 1,
    repeat_rate_pct: 50,
    by_sme: [
      {
        tenant_pseudonym: "SME-K4H2",
        event_count: 2,
        active_days: 2,
        first_seen_at: "2026-07-11T13:51:00Z",
        last_seen_at: "2026-07-12T09:42:00Z",
      },
    ],
  },
  top_recurring_problems: [
    { label: "sales_analysis", count: 1 },
    { label: "inventory_management", count: 1 },
    { label: "finance", count: 1 },
  ],
  most_common_sales_questions: [
    { label: "Which products sold best this week?", count: 1 },
  ],
  most_common_inventory_questions: [
    { label: "What should I restock before the weekend?", count: 1 },
  ],
  most_common_customer_questions: [],
  most_common_advice_requests: [
    { label: "What should I restock before the weekend?", count: 1 },
  ],
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
    artifact_kind: "report",
    status: "ready",
    title: "Weekly research memo · W28",
    summary: "A redacted fixture report for local interface review.",
    created_at: "2026-07-12T10:04:00Z",
    finished_at: "2026-07-12T10:04:02Z",
  },
];

export const previewMcpConnections: McpConnection[] = [];

export const previewMcpRegistry: McpRegistryItem[] = [
  {
    provider: "google_drive",
    name: "Google Drive",
    enabled: false,
    default_mode: "read_only",
    tools: [
      { name: "search_files", label: "Search approved files", kind: "read" },
      { name: "read_file", label: "Read an approved file", kind: "read" },
      { name: "create_file", label: "Create a file", kind: "write" },
    ],
  },
  {
    provider: "google_sheets",
    name: "Google Sheets",
    enabled: false,
    default_mode: "read_only",
    tools: [
      {
        name: "read_sheet",
        label: "Read an approved spreadsheet",
        kind: "read",
      },
      { name: "append_rows", label: "Append spreadsheet rows", kind: "write" },
    ],
  },
  {
    provider: "gmail",
    name: "Gmail",
    enabled: false,
    default_mode: "read_only",
    tools: [
      {
        name: "search_messages",
        label: "Search approved messages",
        kind: "read",
      },
      { name: "read_message", label: "Read an approved message", kind: "read" },
      { name: "send_message", label: "Send a message", kind: "write" },
    ],
  },
  {
    provider: "calendar",
    name: "Google Calendar",
    enabled: false,
    default_mode: "read_only",
    tools: [
      { name: "list_events", label: "List calendar events", kind: "read" },
      { name: "create_event", label: "Create a calendar event", kind: "write" },
    ],
  },
  {
    provider: "meta_ads",
    name: "Meta Ads",
    enabled: false,
    default_mode: "read_only",
    tools: [
      {
        name: "read_campaigns",
        label: "Read campaign performance",
        kind: "read",
      },
      {
        name: "update_campaign_status",
        label: "Change campaign status",
        kind: "write",
      },
    ],
  },
];
