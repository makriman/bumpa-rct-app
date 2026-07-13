import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { previewResearchEvents, previewTenants } from "../lib/preview-fixtures";
import { apiPath, fulfillSession, json, mockBumpa } from "./support";

async function expectWcagAA(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  expect(
    result.violations,
    result.violations
      .map(
        (violation) =>
          `${violation.id}: ${violation.help} (${violation.nodes.length} nodes)`,
      )
      .join("\n"),
  ).toEqual([]);
}

async function expectKeyboardReachable(page: Page, region: Locator) {
  await region.evaluate((target) => {
    const focusable = Array.from(
      document.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]',
      ),
    ).filter((element) => element.getClientRects().length > 0);
    const index = focusable.indexOf(target as HTMLElement);
    if (index < 1) throw new Error("Region has no keyboard predecessor");
    focusable[index - 1].focus();
  });
  await page.keyboard.press("Tab");
  await expect(region).toBeFocused();
  await expect(region).toHaveCSS("outline-style", "solid");
}

async function settleVisuals(page: Page) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

test("public landing and login have zero automated Axe WCAG A/AA violations", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Know your business/i }),
  ).toBeVisible();
  await expectWcagAA(page);

  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await expectWcagAA(page);
});

test("authenticated surfaces have zero automated Axe violations and keyboard-reachable tables", async ({
  page,
}) => {
  await mockBumpa(page);
  await page.goto("/settings/bumpa");
  await expect(
    page.getByRole("heading", { name: "Bumpa data connection" }),
  ).toBeVisible();
  const shellTitle = page.locator(".topbar-title");
  await expect(shellTitle).toHaveText("Bumpa connection");
  expect(
    await shellTitle.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
    "The workspace title must not be visually truncated at this viewport",
  ).toBe(true);
  await expectWcagAA(page);

  await page.unrouteAll({ behavior: "wait" });
  await page.route("**/api/backend/**", async (route) => {
    if (await fulfillSession(route)) return;
    const path = apiPath(route);
    if (path === "/api/backend/admin/tenants") {
      await json(route, previewTenants);
      return;
    }
    if (path === "/api/backend/admin/system/sync-runs") {
      await json(route, []);
      return;
    }
    if (path === "/api/backend/admin/system/errors") {
      await json(route, []);
      return;
    }
    if (path === "/api/backend/admin/usage") {
      await json(route, []);
      return;
    }
    await route.abort("failed");
  });
  await page.goto("/admin");
  await expect(
    page.getByRole("heading", { name: "Platform operations" }),
  ).toBeVisible();
  const tenantHealth = page.getByRole("region", { name: "Tenant health" });
  await expectKeyboardReachable(page, tenantHealth);
  await expectWcagAA(page);

  await page.unrouteAll({ behavior: "wait" });
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
  const questionTable = page.getByRole("region", {
    name: "Research question events",
  });
  await expectKeyboardReachable(page, questionTable);
  await expectWcagAA(page);
});

test("public and authenticated visual baselines do not regress", async ({
  page,
}) => {
  await page.goto("/");
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("public-landing.png", {
    animations: "disabled",
    fullPage: true,
    maxDiffPixels: 200,
  });

  await mockBumpa(page);
  await page.goto("/settings/bumpa");
  await expect(
    page.getByRole("heading", { name: "Bumpa data connection" }),
  ).toBeVisible();
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("sme-bumpa-settings.png", {
    animations: "disabled",
    fullPage: true,
    maxDiffPixels: 200,
  });
});
