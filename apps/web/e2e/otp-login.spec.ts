import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { apiPath, json } from "./support";

test("Nigerian OTP login supports bounded resend and recoverable wrong codes", async ({
  page,
}, testInfo) => {
  let requests = 0;
  let verifications = 0;
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (path === "/api/backend/auth/me") {
      await json(route, { detail: "Authentication required" }, 401);
    } else if (path === "/api/backend/auth/request-otp") {
      expect(route.request().postDataJSON()).toEqual({
        phone_e164: "+2348012345678",
      });
      requests++;
      await json(
        route,
        {
          status: "sent",
          delivery: "whatsapp",
          expires_in_seconds: 600,
          resend_after_seconds: 1,
          dev_code: null,
        },
        202,
      );
    } else if (path === "/api/backend/auth/verify-otp") {
      verifications++;
      await json(route, { detail: "Invalid or expired code" }, 401);
    } else {
      await route.abort("failed");
    }
  });
  await page.goto("/login");
  await expect(
    page.getByRole("button", { name: "Continue securely" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: /Country code/ }).click();
  await page.getByRole("option", { name: /Nigeria.*\+234/ }).click();
  await page.getByLabel("Mobile number").fill("08012345678");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(
    page.getByRole("heading", { name: "Check WhatsApp." }),
  ).toBeVisible();
  await expect(page.getByText(/If this number is approved/)).toBeVisible();
  const code = page.getByLabel("Six-digit WhatsApp code");
  await expect(code).toHaveAttribute("autocomplete", "one-time-code");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const screenshot = testInfo.outputPath("otp-login.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  await testInfo.attach("otp-login-screen", {
    path: screenshot,
    contentType: "image/png",
  });
  await code.fill("111111");
  await page
    .getByRole("button", { name: "Request another code", exact: true })
    .click();
  await expect(code).toHaveValue("");
  expect(requests).toBe(2);
  await code.fill("222222");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.locator("#pin-error")).toHaveAttribute("role", "alert");
  await expect(page.locator("#pin-error")).toContainText(
    "Invalid or expired code",
  );
  await expect(
    page.getByRole("heading", { name: "Check WhatsApp." }),
  ).toBeVisible();
  expect(verifications).toBe(1);
});
