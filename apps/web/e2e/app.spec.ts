import { expect, test } from "@playwright/test";

test("public lander presents the product and reaches login", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Know your business/i }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Talk to your Bestie/i }).click();
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
  await expect(page.getByText("Demo fallback", { exact: true })).toBeVisible();
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
