import type { Page, Route } from "@playwright/test";
import { previewSyncRuns } from "../lib/preview-fixtures";

export async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

export function apiPath(route: Route): string {
  const url = new URL(route.request().url());
  return `${url.pathname}${url.search}`;
}

export const liveSession = {
  user: {
    id: "user-e2e",
    name: "E2E Operator Owner",
    email: "e2e@example.com",
    phone_e164: "+15550102716",
  },
  platform_roles: ["superadmin", "researcher"],
  memberships: [
    {
      id: "membership-e2e",
      tenant_id: "tenant-e2e",
      role: "owner",
      status: "active",
    },
  ],
  current_tenant_id: "tenant-e2e",
};

export async function grantTestSession(page: Page) {
  await page.context().addCookies([
    {
      name: "bb_session",
      value: "e2e-session-fixture",
      url: "http://localhost:3010",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
}

export async function fulfillSession(route: Route): Promise<boolean> {
  if (apiPath(route) !== "/api/backend/auth/me") return false;
  await json(route, liveSession);
  return true;
}

export async function mockBumpa(page: Page) {
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path === "/api/backend/settings/bumpa") {
      await json(route, {
        status: "active",
        provider: "bumpa",
        scope_type: "business_id",
        scope_id_last4: "7712",
        last_successful_sync_at: "2026-07-13T12:00:00Z",
        last_error: null,
      });
      return;
    }
    if (path === "/api/backend/bumpa/sync-runs") {
      await json(route, previewSyncRuns.slice(0, 1));
      return;
    }
    await route.abort("failed");
  });
}
