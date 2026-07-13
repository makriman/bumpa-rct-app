import { expect, test } from "@playwright/test";
import { previewResearchEvents, previewTeam } from "../lib/preview-fixtures";
import {
  apiPath,
  fulfillSession,
  json,
  liveSession,
  mockBumpa,
} from "./support";

test("public lander presents the product and reaches login", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Sign in" })).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: /Know your business/i }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Talk to your Bestie/i }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
});

test("OTP login reaches live SME chat through the browser BFF", async ({
  page,
}) => {
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (path === "/api/backend/auth/request-otp") {
      await json(route, { message: "Code sent", dev_code: "246810" }, 202);
      return;
    }
    if (path === "/api/backend/auth/verify-otp") {
      await json(route, { message: "Verified" });
      return;
    }
    if (path === "/api/backend/auth/me") {
      await json(route, { ...liveSession, platform_roles: [] });
      return;
    }
    if (path === "/api/backend/chat/conversations") {
      await json(route, []);
      return;
    }
    await route.abort("failed");
  });

  await page.goto("/login");
  await page
    .getByRole("textbox", { name: "WhatsApp phone number" })
    .fill("+15550102716");
  await page.getByRole("button", { name: "Send WhatsApp code" }).click();
  await expect(
    page.getByRole("heading", { name: "Check WhatsApp." }),
  ).toBeVisible();
  await expect(page.getByText("Local API code:")).toContainText("246810");
  for (const [index, digit] of [..."246810"].entries()) {
    await page.getByRole("textbox", { name: `Digit ${index + 1}` }).fill(digit);
  }
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText("Tenant API", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Bumpa Bestie" }),
  ).toBeVisible();
});

test("protected user, admin, and research surfaces fail closed without authorization", async ({
  page,
}) => {
  for (const url of [
    "http://app.localhost:3010/chat",
    "http://admin.localhost:3010/admin",
    "http://research.localhost:3010/research/questions",
  ]) {
    await page.goto(url);
    await expect(page).toHaveURL(/\/login\?next=/);
    await expect(
      page.getByRole("heading", { name: "Welcome back." }),
    ).toBeVisible();
  }
});

test("operator onboarding reloads from the durable sync step before unlocking Hermes", async ({
  page,
}) => {
  const syncing = {
    id: "onboarding-e2e",
    tenant_id: "tenant-onboarding-e2e",
    status: "in_progress",
    current_step: "initial_sync",
    revision: 4,
    tenant: {
      id: "tenant-onboarding-e2e",
      slug: "anika-e2e",
      name: "Anika E2E",
      status: "provisioning",
    },
    owner: {
      user_id: "owner-e2e",
      membership_id: "membership-owner-e2e",
      name: "Ada Test",
      email_masked: "a•••@example.test",
      status: "active",
    },
    phone: {
      identity_id: "phone-e2e",
      phone_masked: "+1555••••716",
      label: "Owner",
      status: "active",
      opt_out: false,
    },
    bumpa: {
      connection_id: "bumpa-e2e",
      provider: "bumpa",
      scope_type: "business_id",
      scope_id_last4: "1042",
      status: "active",
    },
    initial_sync: {
      attempt: 1,
      requested_from: "2026-06-13",
      requested_to: "2026-07-13",
      job_id: "job-e2e",
      job_status: "succeeded",
      sync_run_id: "sync-e2e",
      sync_status: "success",
      completion_quality: "complete",
      orders_availability: "available",
      orders_count: 12,
    },
    hermes: null,
    failure: null,
    created_at: "2026-07-13T10:00:00Z",
    updated_at: "2026-07-13T10:05:00Z",
    completed_at: null,
  };
  let projection = syncing;
  let acceptBody: Record<string, unknown> | null = null;
  let acceptHeaders: Record<string, string> | null = null;

  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (
      path === "/api/backend/admin/onboardings/onboarding-e2e" &&
      route.request().method() === "GET"
    ) {
      await json(route, projection);
      return;
    }
    if (
      path ===
        "/api/backend/admin/onboardings/onboarding-e2e/initial-sync/accept" &&
      route.request().method() === "POST"
    ) {
      acceptBody = route.request().postDataJSON() as Record<string, unknown>;
      acceptHeaders = route.request().headers();
      projection = {
        ...syncing,
        current_step: "hermes",
        revision: 5,
      };
      await json(route, projection);
      return;
    }
    await route.abort("failed");
  });

  await page.goto("http://localhost:3010/admin/onboarding/onboarding-e2e");
  await expect(
    page.getByRole("heading", { name: "Initial data sync" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Validate sync and continue →" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: /Provision and verify Hermes/ }),
  ).toHaveCount(0);

  await page.reload();
  await expect(
    page.getByRole("button", { name: "Validate sync and continue →" }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Validate sync and continue →" })
    .click();
  await expect(
    page.getByRole("button", { name: "Provision and verify Hermes →" }),
  ).toBeVisible();
  expect(acceptBody).toEqual({ confirmation: "accept" });
  expect(JSON.stringify(acceptBody)).not.toContain("job-e2e");
  expect(acceptHeaders?.["if-match"]).toBe("4");
  expect(acceptHeaders?.["idempotency-key"]).toBeTruthy();
});

test("team settings adds a persisted live member", async ({ page }) => {
  let team = [...previewTeam];
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (
      path === "/api/backend/settings/team" &&
      route.request().method() === "POST"
    ) {
      const payload = route.request().postDataJSON() as {
        name: string;
        phone_e164: string;
        email: string | null;
        role: string;
      };
      const created = {
        membership_id: "membership-new",
        user_id: "user-new",
        ...payload,
        status: "active",
      };
      team = [...team, created];
      await json(route, created, 201);
      return;
    }
    if (path === "/api/backend/settings/team") {
      await json(route, team);
      return;
    }
    await route.abort("failed");
  });

  await page.goto("/settings/team");
  await expect(page.getByText(/Live team memberships/)).toBeVisible();
  await page.getByRole("button", { name: /Add teammate/ }).click();
  await page.getByLabel("Full name").fill("Tomi Operator");
  await page.getByLabel("Phone in E.164 format").fill("+2348333333333");
  await page.getByLabel("Email (optional)").fill("tomi@example.com");
  await page.getByLabel("Workspace role").selectOption("admin");
  await page.getByRole("button", { name: "Add member" }).click();
  await expect(
    page.getByText("Team member added to this workspace."),
  ).toBeVisible();
  await expect(page.getByText("Tomi Operator")).toBeVisible();
});

test("Bumpa settings renders live connection and sync evidence", async ({
  page,
}) => {
  await mockBumpa(page);
  await page.goto("/settings/bumpa");
  await expect(
    page.getByRole("heading", { name: "Bumpa data connection" }),
  ).toBeVisible();
  await expect(
    page.getByText(/Live Bumpa connection and sync history/),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Connection health" }),
  ).toBeVisible();
  await expect(page.getByText("Business Id · •••• 7712")).toBeVisible();
});

test("research filters update the live question result table", async ({
  page,
}) => {
  await page.route("**/api/backend/**", async (route) => {
    if (await fulfillSession(route)) return;
    if (apiPath(route) === "/api/backend/research/questions") {
      await json(route, previewResearchEvents);
      return;
    }
    await route.abort("failed");
  });
  await page.goto("/research/questions");
  await expect(page.getByText(/Live question events/)).toBeVisible();
  const first = previewResearchEvents[0];
  const second = previewResearchEvents[1];
  await expect(page.getByText(first.redacted_text ?? "")).toBeVisible();
  await page
    .getByRole("textbox", { name: "Search" })
    .fill(second.tenant_pseudonym);
  await expect(page.getByText(second.redacted_text ?? "")).toBeVisible();
  await expect(page.getByText(first.redacted_text ?? "")).toHaveCount(0);
});

test("research report generation can be queued with filters", async ({
  page,
}) => {
  await page.route("**/api/backend/research/reports**", async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as {
        report_type: string;
      };
      await json(
        route,
        {
          id: "report-browser-queue",
          report_type: payload.report_type,
          artifact_kind: "report",
          title: "Browser-queued weekly memo",
          status: "queued",
          created_at: "2026-07-13T12:00:00Z",
          finished_at: null,
        },
        202,
      );
      return;
    }
    await json(route, []);
  });
  await page.route("**/api/backend/auth/me", (route) =>
    json(route, liveSession),
  );

  await page.goto("/research/reports");
  await expect(page.getByText(/Live research artifacts/)).toBeVisible();
  await page.getByRole("button", { name: /Create report/ }).click();
  await page.getByLabel("From date").fill("2026-07-01");
  await page.getByLabel("To date").fill("2026-07-13");
  await page.getByLabel("Channel").selectOption("whatsapp");
  await page.getByRole("button", { name: "Generate report" }).click();
  await expect(
    page.getByText(
      "Report queued. Its status will appear in the artifact list shortly.",
    ),
  ).toBeVisible();
  await expect(page.getByText("Browser-queued weekly memo")).toBeVisible();
});

test("the authenticated shell stays navigable without horizontal overflow", async ({
  page,
}, testInfo) => {
  await mockBumpa(page);
  await page.goto("/settings/bumpa");
  if (testInfo.project.name === "mobile-chromium") {
    await page.getByRole("button", { name: "Open navigation" }).click();
  }
  await expect(
    page.getByRole("link", { name: "Bumpa connection" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
