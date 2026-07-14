import { expect, test, type Page } from "@playwright/test";
import { previewResearchEvents, previewTeam } from "../lib/preview-fixtures";
import {
  apiPath,
  fulfillSession,
  json,
  liveSession,
  mockBumpa,
} from "./support";

async function selectCountry(page: Page, iso: string) {
  await page.getByRole("button", { name: /Country code/ }).click();
  await page.locator(`[role="option"][data-country-iso="${iso}"]`).click();
}

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

test("temporary web PIN login reaches live SME chat through the browser BFF", async ({
  page,
}) => {
  let releaseRequest: (() => void) | undefined;
  const requestGate = new Promise<void>((resolve) => {
    releaseRequest = resolve;
  });
  let markRequestObserved: (() => void) | undefined;
  const requestObserved = new Promise<void>((resolve) => {
    markRequestObserved = resolve;
  });
  let releaseVerification: (() => void) | undefined;
  const verificationGate = new Promise<void>((resolve) => {
    releaseVerification = resolve;
  });
  let markVerificationObserved: (() => void) | undefined;
  const verificationObserved = new Promise<void>((resolve) => {
    markVerificationObserved = resolve;
  });
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (path === "/api/backend/auth/request-otp") {
      expect(route.request().postDataJSON()).toEqual({
        phone_e164: "+12025550123",
      });
      markRequestObserved?.();
      await requestGate;
      await json(
        route,
        {
          delivery: "web_pin",
          expires_in_seconds: 600,
          dev_code: null,
        },
        202,
      );
      return;
    }
    if (path === "/api/backend/auth/verify-otp") {
      expect(route.request().postDataJSON()).toEqual({
        phone_e164: "+12025550123",
        code: "246810",
      });
      markVerificationObserved?.();
      await verificationGate;
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
  await selectCountry(page, "US");
  await page.getByLabel("Mobile number").fill("202 555 0123");
  const submitButton = page.locator(".auth-submit");
  const continueClick = submitButton.click();
  await requestObserved;
  await expect(submitButton).toHaveText("Checking number…");
  await expect(submitButton).toBeDisabled();
  releaseRequest?.();
  await continueClick;
  await expect(
    page.getByRole("heading", { name: "Enter your web PIN." }),
  ).toBeVisible();
  await expect(page.getByText(/no WhatsApp message was sent/i)).toBeVisible();
  await page.getByLabel("Six-digit web PIN").fill("246810");
  const signInClick = submitButton.click();
  await verificationObserved;
  await expect(submitButton).toHaveText("Signing in…");
  await expect(submitButton).toBeDisabled();
  releaseVerification?.();
  await signInClick;
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText("Tenant API", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Bumpa Bestie" }),
  ).toBeVisible();
});

test("country-aware sign-in validates input and normalizes UK and India numbers", async ({
  page,
}) => {
  const requestedPhones: string[] = [];
  await page.route("**/api/backend/auth/request-otp", async (route) => {
    requestedPhones.push(route.request().postDataJSON().phone_e164 as string);
    await json(
      route,
      { delivery: "web_pin", expires_in_seconds: 600, dev_code: null },
      202,
    );
  });

  await page.goto("/login");
  await page.getByLabel("Mobile number").fill("+44 7400 123456");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page.locator("#login-error")).toContainText(
    "Enter only the number after +44.",
  );
  expect(requestedPhones).toEqual([]);

  await page.getByLabel("Mobile number").fill("07400 123456");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(
    page.getByRole("heading", { name: "Enter your web PIN." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Change number" }).click();

  await selectCountry(page, "IN");
  await page.getByLabel("Mobile number").fill("98765 43210");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(
    page.getByRole("heading", { name: "Enter your web PIN." }),
  ).toBeVisible();
  expect(requestedPhones).toEqual(["+447400123456", "+919876543210"]);
});

test("country picker supports search and keyboard selection", async ({
  page,
}) => {
  await page.goto("/login");
  const trigger = page.getByRole("button", {
    name: "Country code, United Kingdom +44",
  });

  await trigger.click();
  const search = page.getByRole("textbox", {
    name: "Search countries or calling codes",
  });
  await expect(search).toBeFocused();
  await search.fill("India");
  await search.press("Enter");

  const indiaTrigger = page.getByRole("button", {
    name: "Country code, India +91",
  });
  await expect(indiaTrigger).toBeVisible();
  await expect(indiaTrigger).toBeFocused();
  await expect(page.getByLabel("Mobile number")).toHaveAttribute(
    "placeholder",
    "98765 43210",
  );
  await expect(page.getByRole("listbox")).toHaveCount(0);

  await indiaTrigger.click();
  const reopenedSearch = page.getByRole("textbox", {
    name: "Search countries or calling codes",
  });
  for (let index = 0; index < 18; index += 1) {
    await reopenedSearch.press("ArrowDown");
  }
  expect(
    await page.locator(".country-code-option.active").evaluate((element) => {
      const optionBounds = element.getBoundingClientRect();
      const listBounds = element.parentElement?.getBoundingClientRect();
      return Boolean(
        listBounds &&
          optionBounds.top >= listBounds.top &&
          optionBounds.bottom <= listBounds.bottom,
      );
    }),
  ).toBe(true);
  await reopenedSearch.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(indiaTrigger).toBeFocused();
});

test("country picker menu fits a compact mobile viewport", async ({ page }) => {
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 390, height: 420 },
    { width: 844, height: 390 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto(`/login?viewport=${viewport.width}x${viewport.height}`);
    await page
      .getByRole("button", { name: "Country code, United Kingdom +44" })
      .click();

    const popover = page.locator(".country-code-popover");
    await expect(popover).toBeVisible();
    expect(
      await popover.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return {
          horizontal: bounds.left >= 0 && bounds.right <= window.innerWidth,
          vertical: bounds.top >= 0 && bounds.bottom <= window.innerHeight,
        };
      }),
    ).toEqual({ horizontal: true, vertical: true });
  }
});

test("country picker follows the visual viewport while open", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 667 });
  await page.goto("/login?visual-viewport");
  const trigger = page.getByRole("button", {
    name: "Country code, United Kingdom +44",
  });
  const triggerBounds = await trigger.boundingBox();
  expect(triggerBounds).not.toBeNull();
  const initialViewportHeight = 360;
  const initialViewportTop = Math.min(
    Math.max(0, (triggerBounds?.y ?? 0) - 100),
    667 - initialViewportHeight,
  );
  await page.evaluate(
    ({ height, offsetTop }) => {
      const visualViewport = new EventTarget();
      Object.defineProperties(visualViewport, {
        height: { configurable: true, value: height },
        offsetTop: { configurable: true, value: offsetTop },
        width: { configurable: true, value: 390 },
      });
      Object.defineProperty(window, "visualViewport", {
        configurable: true,
        value: visualViewport,
      });
    },
    { height: initialViewportHeight, offsetTop: initialViewportTop },
  );
  await trigger.click();

  const popover = page.locator(".country-code-popover");
  const fitsVisualViewport = () =>
    popover.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      const viewport = window.visualViewport;
      return Boolean(
        viewport &&
          bounds.top >= viewport.offsetTop &&
          bounds.bottom <= viewport.offsetTop + viewport.height,
      );
    });
  await expect.poll(fitsVisualViewport).toBe(true);

  const resizedViewportHeight = 300;
  const resizedViewportTop = Math.min(
    Math.max(0, (triggerBounds?.y ?? 0) - 72),
    667 - resizedViewportHeight,
  );
  await page.evaluate(
    ({ height, offsetTop }) => {
      const viewport = window.visualViewport;
      if (!viewport) return;
      Object.defineProperties(viewport, {
        height: { configurable: true, value: height },
        offsetTop: { configurable: true, value: offsetTop },
      });
      viewport.dispatchEvent(new Event("resize"));
      viewport.dispatchEvent(new Event("scroll"));
    },
    { height: resizedViewportHeight, offsetTop: resizedViewportTop },
  );
  await expect.poll(fitsVisualViewport).toBe(true);

  const constrainedViewportHeight = 120;
  const constrainedViewportTop = Math.min(
    Math.max(0, (triggerBounds?.y ?? 0) - 36),
    667 - constrainedViewportHeight,
  );
  await page.evaluate(
    ({ height, offsetTop }) => {
      const viewport = window.visualViewport;
      if (!viewport) return;
      Object.defineProperties(viewport, {
        height: { configurable: true, value: height },
        offsetTop: { configurable: true, value: offsetTop },
      });
      viewport.dispatchEvent(new Event("resize"));
    },
    {
      height: constrainedViewportHeight,
      offsetTop: constrainedViewportTop,
    },
  );
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(trigger).toBeFocused();

  await page.evaluate(
    ({ height, offsetTop }) => {
      const viewport = window.visualViewport;
      if (!viewport) return;
      Object.defineProperties(viewport, {
        height: { configurable: true, value: height },
        offsetTop: { configurable: true, value: offsetTop },
      });
      viewport.dispatchEvent(new Event("resize"));
    },
    { height: initialViewportHeight, offsetTop: initialViewportTop },
  );
  await trigger.click();
  await expect(page.getByRole("listbox")).toBeVisible();

  await page.evaluate(
    ({ height }) => {
      const viewport = window.visualViewport;
      if (!viewport) return;
      Object.defineProperties(viewport, {
        height: { configurable: true, value: height },
        offsetTop: { configurable: true, value: 0 },
      });
      viewport.dispatchEvent(new Event("scroll"));
    },
    { height: Math.max(1, Math.floor((triggerBounds?.y ?? 1) - 1)) },
  );
  await expect(page.getByRole("listbox")).toHaveCount(0);
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
