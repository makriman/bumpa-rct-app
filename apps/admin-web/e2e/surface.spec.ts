import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import {
  previewAudits,
  previewDeadLetterJobs,
  previewErrors,
  previewPlatformAdmins,
  previewSyncRuns,
  previewTenants,
  previewUsage,
} from "../lib/preview-fixtures";

const routes = [
  "/",
  "/login",
  "/tenants",
  "/tenants/demo-kaia-home",
  "/administrators",
  "/connections",
  "/sync-runs",
  "/failures",
  "/provider-failures",
  "/usage",
  "/onboarding",
  "/onboarding/demo-onboarding",
];

function routeSnapshotName(path: string): string {
  const slug = path === "/" ? "overview" : path.slice(1).replaceAll("/", "-");
  return `route-${slug}.png`;
}

function failOnBrowserErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      errors.push(`${message.type()}: ${message.text()}`);
    }
  });
  return errors;
}

async function settleDocumentGeometry(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;

    let previousHeight = -1;
    let stableSamples = 0;
    for (let sample = 0; sample < 20; sample += 1) {
      const height = Math.max(
        document.documentElement.scrollHeight,
        document.body.scrollHeight,
      );
      stableSamples = height === previousHeight ? stableSamples + 1 : 0;
      if (stableSamples >= 2) return;
      previousHeight = height;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }

    throw new Error("Document geometry did not stabilize before capture");
  });
}

async function grantAdminSession(page: Page, value = "e2e-admin-session") {
  await page.context().addCookies([
    {
      name: "bb_session",
      value,
      url: "http://localhost:3011",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

const adminSession = {
  user: {
    id: "admin-e2e",
    name: "E2E platform operator",
    email: null,
    phone_e164: "+12025550124",
  },
  platform_roles: ["operator", "superadmin"],
  memberships: [],
  current_tenant_id: null,
};

const onboardingFixture = {
  id: "demo-onboarding",
  tenant_id: "demo-kaia-home",
  status: "in_progress",
  current_step: "owner",
  revision: 1,
  tenant: {
    id: "demo-kaia-home",
    slug: "kaia-home",
    name: "Kaia Home",
    status: "provisioning",
    timezone: "Africa/Lagos",
    currency_code: "NGN",
  },
  owner: null,
  phone: null,
  bumpa: null,
  initial_sync: null,
  hermes: null,
  failure: null,
  created_at: "2026-07-21T08:00:00Z",
  updated_at: "2026-07-21T08:00:00Z",
  completed_at: null,
};

const tenantOperationsFixture = {
  tenant_id: "demo-kaia-home",
  people: [],
  phones: [],
  bumpa: {
    connected: true,
    status: "active",
    scope_type: "business_id",
    scope_id_last4: "7712",
    store_timezone: "Africa/Lagos",
    store_currency: "NGN",
    provider: "bumpa",
    last_successful_sync_at: "2026-07-21T07:30:00Z",
    last_failed_sync_at: null,
    last_error: null,
  },
  hermes: {
    provisioned: true,
    profile_name: "kaia-home",
    provider: "hermes",
    status: "active",
    api_port: 8701,
  },
};

async function mockAdminData(
  page: Page,
  connectionResponse:
    | { status: number; body: unknown }
    | ((route: Route) => Promise<void>) = {
    status: 200,
    body: [],
  },
) {
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\/backend/, "");
    if (path === "/auth/me") {
      await fulfillJson(route, adminSession);
      return;
    }
    if (path === "/admin/tenants") {
      await fulfillJson(route, previewTenants);
      return;
    }
    if (path === "/admin/tenants/demo-kaia-home") {
      await fulfillJson(route, previewTenants[0]);
      return;
    }
    if (path === "/admin/tenants/demo-kaia-home/operations") {
      await fulfillJson(route, tenantOperationsFixture);
      return;
    }
    if (path === "/admin/audit") {
      await fulfillJson(route, previewAudits);
      return;
    }
    if (path === "/admin/platform-access") {
      await fulfillJson(route, previewPlatformAdmins);
      return;
    }
    if (path === "/admin/system/sync-runs") {
      await fulfillJson(route, previewSyncRuns);
      return;
    }
    if (path === "/admin/system/errors") {
      await fulfillJson(route, previewErrors);
      return;
    }
    if (path === "/admin/system/jobs") {
      await fulfillJson(route, previewDeadLetterJobs);
      return;
    }
    if (
      path === "/admin/system/whatsapp-delivery-failures" ||
      path === "/admin/system/hermes-call-errors"
    ) {
      await fulfillJson(route, []);
      return;
    }
    if (path === "/admin/usage") {
      await fulfillJson(route, previewUsage);
      return;
    }
    if (path === "/admin/mcp-connections") {
      if (typeof connectionResponse === "function") {
        await connectionResponse(route);
        return;
      }
      await fulfillJson(
        route,
        connectionResponse.body,
        connectionResponse.status,
      );
      return;
    }
    if (path === "/admin/onboardings") {
      await fulfillJson(route, [onboardingFixture]);
      return;
    }
    if (path === "/admin/onboardings/demo-onboarding") {
      await fulfillJson(route, onboardingFixture);
      return;
    }
    await route.abort("failed");
  });
}

for (const route of routes) {
  test(`${route} is accessible, bounded, and free of browser errors`, async ({
    browserName,
    page,
  }) => {
    if (route !== "/login") {
      await grantAdminSession(page);
      await mockAdminData(page);
    }
    const errors = failOnBrowserErrors(page);
    const response = await page.goto(route);
    expect(response?.status()).toBe(200);
    await expect(page.locator("main")).toBeVisible();
    await page.emulateMedia({ reducedMotion: "reduce" });
    await settleDocumentGeometry(page);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const results = await new AxeBuilder({ page })
      .withTags([
        "wcag2a",
        "wcag2aa",
        "wcag21a",
        "wcag21aa",
        "wcag22a",
        "wcag22aa",
      ])
      .analyze();
    expect(
      results.violations,
      results.violations.map((item) => `${item.id}: ${item.help}`).join("\n"),
    ).toEqual([]);
    await settleDocumentGeometry(page);
    await expect(page).toHaveScreenshot(routeSnapshotName(route), {
      animations: "disabled",
      fullPage: true,
      maxDiffPixels: 200,
    });
    const unexplainedErrors = errors.filter(
      (error) =>
        !(
          browserName === "webkit" &&
          error.startsWith(
            "error: Refused to apply a stylesheet because its hash",
          )
        ),
    );
    expect(unexplainedErrors).toEqual([]);
  });
}

test("operator login establishes a server-issued session and returns to the protected route", async ({
  page,
}) => {
  await page.goto("/tenants");
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByRole("button", { name: /Country code/i }).click();
  await page
    .getByRole("option", { name: "United States +1", exact: true })
    .click();
  await page.getByRole("textbox", { name: "Mobile number" }).fill("2025550124");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByRole("textbox", { name: "Six-digit code" }).fill("246811");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL("/tenants");
  await expect(
    page.getByRole("heading", { name: "SME tenants" }),
  ).toBeVisible();
  const cookies = await page.context().cookies();
  expect(cookies.find((cookie) => cookie.name === "bb_session")).toMatchObject({
    httpOnly: true,
    sameSite: "Lax",
  });
});

test("tenant filters persist in the URL and survive reload", async ({
  page,
}) => {
  await grantAdminSession(page);
  await mockAdminData(page);
  await page.goto("/tenants");
  await page.getByRole("textbox", { name: "Search" }).fill("Lagos");
  await page
    .getByRole("combobox", { name: "Filter by status" })
    .selectOption("active");
  await expect(page).toHaveURL(/q=Lagos/);
  await expect(page).toHaveURL(/status=active/);

  await page.reload();
  await expect(page.getByRole("textbox", { name: "Search" })).toHaveValue(
    "Lagos",
  );
  await expect(
    page.getByRole("combobox", { name: "Filter by status" }),
  ).toHaveValue("active");
  await expect(page.getByRole("row", { name: /Kaia Home/ })).toBeVisible();
});

test("connection approvals expose loading, empty, error, and retry states", async ({
  page,
}) => {
  let releaseLoading: () => void = () => undefined;
  const loadingGate = new Promise<void>((resolve) => {
    releaseLoading = resolve;
  });
  await grantAdminSession(page);
  await mockAdminData(page, async (route) => {
    await loadingGate;
    await fulfillJson(route, []);
  });
  await page.goto("/connections");
  await expect(page.locator('[aria-busy="true"]')).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page).toHaveScreenshot("state-connections-loading.png", {
    animations: "disabled",
    fullPage: true,
    maxDiffPixels: 200,
  });
  releaseLoading();
  await expect(
    page.getByRole("heading", { name: "No matching connection requests" }),
  ).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  let attempts = 0;
  await mockAdminData(page, async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await fulfillJson(
        route,
        {
          detail: {
            code: "service_unavailable",
            message: "Connection approvals are temporarily unavailable.",
            retryable: true,
          },
        },
        503,
      );
      return;
    }
    await fulfillJson(route, []);
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Something went wrong" }),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("state-connections-error.png", {
    animations: "disabled",
    fullPage: true,
    maxDiffPixels: 200,
  });
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("heading", { name: "No matching connection requests" }),
  ).toBeVisible();
  expect(attempts).toBe(2);
});

test("admin routes fail closed without a session or platform role", async ({
  page,
}) => {
  await page.goto("/tenants");
  await expect(page).toHaveURL(/\/login\?next=/);

  await grantAdminSession(page, "e2e-wrong-role");
  await page.goto("/tenants");
  await expect(page).toHaveURL(/\/login\?next=/);
});

test("legacy admin links permanently redirect to clean routes", async ({
  request,
}) => {
  const response = await request.get("/admin/tenants", { maxRedirects: 0 });
  expect(response.status()).toBe(308);
  expect(
    new URL(response.headers().location ?? "", response.url()).pathname,
  ).toBe("/tenants");
});
