import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import {
  previewReports,
  previewResearchConversationDetails,
  previewResearchConversations,
  previewResearchEvents,
  previewResearchOverview,
  previewTaxonomy,
} from "../lib/preview-fixtures";

const routes = [
  "/",
  "/login",
  "/questions",
  "/conversations",
  "/classifications",
  "/cohorts",
  "/reports",
  "/exports",
  "/consent",
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

async function grantResearchSession(
  page: Page,
  value = "e2e-research-session",
) {
  await page.context().addCookies([
    {
      name: "bb_session",
      value,
      url: "http://localhost:3012",
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

const researchSession = {
  user: {
    id: "research-e2e",
    name: "E2E researcher",
    email: null,
    phone_e164: "+12025550125",
  },
  platform_roles: ["researcher"],
  memberships: [],
  current_tenant_id: null,
};

async function mockResearchData(
  page: Page,
  conversationResponse:
    | { status: number; body: unknown }
    | ((route: Route) => Promise<void>) = {
    status: 200,
    body: previewResearchConversations,
  },
) {
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\/backend/, "");
    if (path === "/auth/me") {
      await fulfillJson(route, researchSession);
      return;
    }
    if (path === "/research/overview") {
      await fulfillJson(route, previewResearchOverview);
      return;
    }
    if (path === "/research/questions" || path === "/research/events") {
      await fulfillJson(route, previewResearchEvents);
      return;
    }
    if (path === "/research/conversations") {
      if (typeof conversationResponse === "function") {
        await conversationResponse(route);
        return;
      }
      await fulfillJson(
        route,
        conversationResponse.body,
        conversationResponse.status,
      );
      return;
    }
    if (path.startsWith("/research/conversations/")) {
      const id = decodeURIComponent(path.split("/").at(-1) ?? "");
      await fulfillJson(
        route,
        previewResearchConversationDetails[id] ?? {
          detail: "Conversation not found",
        },
        previewResearchConversationDetails[id] ? 200 : 404,
      );
      return;
    }
    if (path === "/research/taxonomy") {
      await fulfillJson(route, previewTaxonomy);
      return;
    }
    if (path === "/research/reports") {
      await fulfillJson(route, previewReports);
      return;
    }
    if (path.startsWith("/research/reports/")) {
      await fulfillJson(route, {
        ...previewReports[0],
        filters: {},
        artifacts: [],
      });
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
    if (!["/login", "/consent"].includes(route)) {
      await grantResearchSession(page);
      await mockResearchData(page);
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

test("researcher login establishes a server-issued session and returns to the protected route", async ({
  page,
}) => {
  await page.goto("/questions");
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByRole("button", { name: /Country code/i }).click();
  await page
    .getByRole("option", { name: "United States +1", exact: true })
    .click();
  await page.getByRole("textbox", { name: "Mobile number" }).fill("2025550125");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByRole("textbox", { name: "Six-digit code" }).fill("246812");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL("/questions");
  await expect(
    page.getByRole("heading", { name: "Question log" }),
  ).toBeVisible();
  const cookies = await page.context().cookies();
  expect(cookies.find((cookie) => cookie.name === "bb_session")).toMatchObject({
    httpOnly: true,
    sameSite: "Lax",
  });
});

test("question filters persist in the URL and survive reload", async ({
  page,
}) => {
  await grantResearchSession(page);
  await mockResearchData(page);
  await page.goto("/questions");
  await page.getByRole("textbox", { name: "Search" }).fill("products");
  await page
    .getByRole("combobox", { name: "Filter by intent" })
    .selectOption("sales_analysis");
  await expect(page).toHaveURL(/q=products/);
  await expect(page).toHaveURL(/intent=sales_analysis/);

  await page.reload();
  await expect(page.getByRole("textbox", { name: "Search" })).toHaveValue(
    "products",
  );
  await expect(
    page.getByRole("combobox", { name: "Filter by intent" }),
  ).toHaveValue("sales_analysis");
  await expect(
    page.getByRole("row", { name: /Which products sold best/ }),
  ).toBeVisible();
});

test("conversation research exposes loading, empty, error, and retry states", async ({
  page,
}) => {
  let releaseLoading: () => void = () => undefined;
  const loadingGate = new Promise<void>((resolve) => {
    releaseLoading = resolve;
  });
  await grantResearchSession(page);
  await mockResearchData(page, async (route) => {
    await loadingGate;
    await fulfillJson(route, []);
  });
  await page.goto("/conversations");
  await expect(page.locator('[aria-busy="true"]')).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page).toHaveScreenshot("state-conversations-loading.png", {
    animations: "disabled",
    fullPage: true,
    maxDiffPixels: 200,
  });
  releaseLoading();
  await expect(
    page.getByRole("heading", { name: "No consented conversations yet" }),
  ).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  let attempts = 0;
  await mockResearchData(page, async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await fulfillJson(
        route,
        {
          detail: {
            code: "service_unavailable",
            message: "Research conversations are temporarily unavailable.",
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
  await expect(page).toHaveScreenshot("state-conversations-error.png", {
    animations: "disabled",
    fullPage: true,
    maxDiffPixels: 200,
  });
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("heading", { name: "No consented conversations yet" }),
  ).toBeVisible();
  expect(attempts).toBe(2);
});

test("research routes fail closed without a session or research role", async ({
  page,
}) => {
  await page.goto("/questions");
  await expect(page).toHaveURL(/\/login\?next=/);

  await grantResearchSession(page, "e2e-wrong-role");
  await page.goto("/questions");
  await expect(page).toHaveURL(/\/login\?next=/);
});

test("legacy research links permanently redirect to clean routes", async ({
  request,
}) => {
  const response = await request.get("/research/questions", {
    maxRedirects: 0,
  });
  expect(response.status()).toBe(308);
  expect(
    new URL(response.headers().location ?? "", response.url()).pathname,
  ).toBe("/questions");
});
