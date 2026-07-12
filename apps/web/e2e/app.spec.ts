import { expect, test } from "@playwright/test";

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

test("local demo can enter the SME chat with an explicit fallback label", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "SME owner" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText("Demo preview", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Bumpa Bestie" }),
  ).toBeVisible();
});

test("admin and research hosts keep login reachable", async ({ page }) => {
  for (const host of ["admin.localhost", "research.localhost"]) {
    await page.goto(`http://${host}:3010/login`);
    await expect(
      page.getByRole("heading", { name: "Welcome back." }),
    ).toBeVisible();
  }
});

test("demo admin, research, and settings rows are never labelled live", async ({
  page,
}) => {
  for (const [path, fixture] of [
    ["/admin/tenants", "Kaia Home"],
    ["/research/questions", "Which products sold best this week?"],
    ["/settings/team", "Amara Okafor"],
  ]) {
    await page.goto(path);
    await expect(page.getByText(/Demo preview/).first()).toBeVisible();
    await expect(
      page.getByText(fixture, { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByText(/Live .*API connected/)).toHaveCount(0);
  }
});

test("the authenticated shell stays navigable without horizontal overflow", async ({
  page,
}, testInfo) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "SME owner" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.locator(".environment")).toHaveText("DEMO DATA");

  if (testInfo.project.name === "mobile-chromium") {
    await page.getByRole("button", { name: "Open navigation" }).click();
  }
  await page.getByRole("link", { name: "Bumpa connection" }).click();
  await expect(page).toHaveURL(/\/settings\/bumpa$/);
  await expect(
    page.getByRole("heading", { name: "Bumpa data connection" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
