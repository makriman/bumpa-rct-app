import type { SyncRun, TeamMember } from "./platform-data";

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
