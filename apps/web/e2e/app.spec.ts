import { expect, test, type Page } from "@playwright/test";
import { previewTeam } from "../lib/preview-fixtures";
import {
  apiPath,
  fulfillSession,
  grantTestSession,
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
  await expect(
    page.getByRole("button", { name: "Country code, United States +1" }),
  ).toBeVisible();
  const mobileNumber = page.getByLabel("Mobile number");
  await mobileNumber.fill("202 555 0123");
  await expect(mobileNumber).toHaveValue("202 555 0123");
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
  await grantTestSession(page);
  const signInClick = submitButton.click();
  await verificationObserved;
  await expect(submitButton).toHaveText("Signing in…");
  await expect(submitButton).toBeDisabled();
  releaseVerification?.();
  await signInClick;
  await expect(page).toHaveURL(/\/chat$/);
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
  const mobileNumber = page.getByLabel("Mobile number");
  await mobileNumber.fill("+44 7400 123456");
  await expect(mobileNumber).toHaveValue("+44 7400 123456");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page.locator("#login-error")).toContainText(
    "Enter only the number after +44.",
  );
  expect(requestedPhones).toEqual([]);

  await mobileNumber.fill("07400 123456");
  await expect(mobileNumber).toHaveValue("07400 123456");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(
    page.getByRole("heading", { name: "Enter your web PIN." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Change number" }).click();

  await selectCountry(page, "IN");
  await expect(
    page.getByRole("button", { name: "Country code, India +91" }),
  ).toBeVisible();
  await mobileNumber.fill("98765 43210");
  await expect(mobileNumber).toHaveValue("98765 43210");
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

test("protected consumer surfaces fail closed without a session", async ({
  page,
}) => {
  for (const path of ["/chat", "/profile", "/settings/team"]) {
    await page.goto(`http://app.localhost:3010${path}`);
    await expect(page).toHaveURL(/\/login\?next=/);
    await expect(
      page.getByRole("heading", { name: "Welcome back." }),
    ).toBeVisible();
  }
});

test("chat supports retry, durable URLs, history paging, and the mobile drawer", async ({
  page,
}, testInfo) => {
  await grantTestSession(page);
  let sendAttempts = 0;
  const clientMessageIds: string[] = [];
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, {
        items: [
          {
            id: "conversation-existing",
            title: "Quarterly planning",
            updated_at: "2026-07-21T08:00:00Z",
            channel: "web",
            last_message_preview: "Plan the next quarter",
          },
        ],
        next_cursor: "cursor-2",
      });
      return;
    }
    if (path.includes("cursor=cursor-2")) {
      await json(route, {
        items: [
          {
            id: "conversation-older",
            title: "Older stock review",
            updated_at: "2026-06-21T08:00:00Z",
            channel: "web",
            last_message_preview: "Review older stock",
          },
        ],
        next_cursor: null,
      });
      return;
    }
    if (
      path ===
      "/api/backend/chat/conversations/conversation-existing/messages?limit=50"
    ) {
      await json(route, {
        items: [
          {
            id: "message-existing",
            direction: "outbound",
            content: "Your quarterly plan is ready to review.",
            created_at: "2026-07-21T08:01:00Z",
          },
        ],
        next_cursor: null,
      });
      return;
    }
    if (
      path ===
      "/api/backend/chat/conversations/conversation-new/messages?limit=50"
    ) {
      await json(route, {
        items: [
          {
            id: "message-inbound",
            direction: "inbound",
            content: "What should I restock first?",
            created_at: "2026-07-21T08:29:00Z",
          },
          {
            id: "message-outbound",
            direction: "outbound",
            content:
              "Start with the products that sell quickly and have the longest supplier lead time.",
            created_at: "2026-07-21T08:30:00Z",
          },
        ],
        next_cursor: null,
      });
      return;
    }
    if (path === "/api/backend/chat/web") {
      sendAttempts += 1;
      const payload = route.request().postDataJSON() as {
        client_message_id: string;
      };
      clientMessageIds.push(payload.client_message_id);
      if (sendAttempts === 1) {
        await json(
          route,
          {
            detail: {
              code: "provider_unavailable",
              message: "The assistant is temporarily unavailable.",
              retryable: true,
            },
          },
          503,
        );
        return;
      }
      await json(route, {
        answer:
          "Start with the products that sell quickly and have the longest supplier lead time.",
        conversation_id: "conversation-new",
        inbound_message_id: "message-inbound",
        outbound_message_id: "message-outbound",
        data_freshness: "2026-07-21T08:30:00Z",
      });
      return;
    }
    await route.abort("failed");
  });

  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "Message Bumpa Bestie" });
  await composer.fill("What should I restock first?");
  await expect(composer).toHaveValue("What should I restock first?");
  if (testInfo.project.name === "mobile-chromium") {
    await page.getByRole("button", { name: "Send message" }).click();
  } else {
    await composer.press("Enter");
  }
  await expect(page.getByText("Your message was not sent.")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByText(/Start with the products that sell quickly/),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/conversation-new$/);
  expect(clientMessageIds).toHaveLength(2);
  expect(clientMessageIds[1]).toBe(clientMessageIds[0]);

  if (testInfo.project.name === "mobile-chromium") {
    await page
      .getByRole("button", { name: "Open conversation history" })
      .click();
  }
  await page.getByRole("button", { name: /Quarterly planning/ }).click();
  await expect(page).toHaveURL(/\/chat\/conversation-existing$/);
  await expect(
    page.getByText("Your quarterly plan is ready to review."),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("Your quarterly plan is ready to review."),
  ).toBeVisible();

  if (testInfo.project.name === "mobile-chromium") {
    await page
      .getByRole("button", { name: "Open conversation history" })
      .click();
  }
  await page.getByRole("button", { name: "Show more" }).click();
  await expect(
    page.getByRole("button", { name: /Older stock review/ }),
  ).toBeVisible();
});

test("chat recovers after the browser returns online", async ({
  page,
}, testInfo) => {
  await grantTestSession(page);
  let sendAttempts = 0;
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    if (
      path ===
      "/api/backend/chat/conversations/conversation-recovered/messages?limit=50"
    ) {
      await json(route, {
        items: [
          {
            id: "message-recovered-inbound",
            direction: "inbound",
            content: "What should I prioritise?",
            created_at: "2026-07-21T08:29:00Z",
          },
          {
            id: "message-recovered-outbound",
            direction: "outbound",
            content:
              "You are back online. Start with the products selling fastest.",
            created_at: "2026-07-21T08:30:00Z",
          },
        ],
        next_cursor: null,
      });
      return;
    }
    if (path === "/api/backend/chat/web") {
      sendAttempts += 1;
      if (sendAttempts === 1) {
        await route.abort("internetdisconnected");
        return;
      }
      await json(route, {
        answer: "You are back online. Start with the products selling fastest.",
        conversation_id: "conversation-recovered",
        inbound_message_id: "message-recovered-inbound",
        outbound_message_id: "message-recovered-outbound",
        data_freshness: null,
      });
      return;
    }
    await route.abort("failed");
  });

  await page.goto("/chat");
  const composer = page.getByRole("textbox", {
    name: "Message Bumpa Bestie",
  });
  await composer.fill("What should I prioritise?");
  await expect(composer).toHaveValue("What should I prioritise?");
  if (testInfo.project.name === "mobile-chromium") {
    await page.getByRole("button", { name: "Send message" }).click();
  } else {
    await composer.press("Enter");
  }
  await expect(page.getByText("Your message was not sent.")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText(/You are back online/)).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/conversation-recovered$/);
});

test("account menu logs out through the browser BFF", async ({
  page,
}, testInfo) => {
  await grantTestSession(page);
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    if (path === "/api/backend/auth/logout") {
      await json(route, { message: "Logged out" });
      return;
    }
    await route.abort("failed");
  });

  await page.goto("/chat");
  if (testInfo.project.name === "mobile-chromium") {
    await page
      .getByRole("button", { name: "Open conversation history" })
      .click();
  }
  await page.getByText("Your account", { exact: true }).click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("chat reflows at the CSS viewport equivalent of 200% desktop zoom", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");
  await grantTestSession(page);
  await page.route("**/api/backend/**", async (route) => {
    if (await fulfillSession(route)) return;
    if (apiPath(route) === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    await route.abort("failed");
  });

  await page.setViewportSize({ width: 720, height: 450 });
  await page.goto("/chat");
  await expect(
    page.getByRole("button", { name: "Open conversation history" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("primary mobile chat controls keep generous touch targets", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium");
  await grantTestSession(page);
  await page.route("**/api/backend/**", async (route) => {
    if (await fulfillSession(route)) return;
    if (apiPath(route) === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    await route.abort("failed");
  });

  await page.goto("/chat");
  const primaryControls = [
    page.getByRole("button", { name: "Open conversation history" }),
    page.getByRole("button", { name: "Start a new chat" }),
    page.getByRole("button", { name: "Send message" }),
  ];
  for (const control of primaryControls) {
    const bounds = await control.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds?.width).toBeGreaterThanOrEqual(38);
    expect(bounds?.height).toBeGreaterThanOrEqual(38);
  }
  await primaryControls[0].click();
  const drawerControls = [
    page.getByRole("button", { name: "Close conversation history" }),
    page.getByRole("button", { name: "New chat", exact: true }),
    page.locator('summary[aria-label="Open account menu"]'),
  ];
  for (const control of drawerControls) {
    const bounds = await control.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds?.width).toBeGreaterThanOrEqual(40);
    expect(bounds?.height).toBeGreaterThanOrEqual(40);
  }
});

test("team settings adds a persisted live member", async ({ page }) => {
  await grantTestSession(page);
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
  await grantTestSession(page);
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

test("consumer output contains no links to operational surfaces", async ({
  page,
}) => {
  await page.route("**/api/backend/**", async (route) => {
    if (await fulfillSession(route)) return;
    if (apiPath(route) === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    await route.abort("failed");
  });

  for (const path of ["/", "/chat"]) {
    if (path === "/chat") await grantTestSession(page);
    await page.goto(path);
    const links = await page
      .locator("a")
      .evaluateAll((anchors) =>
        anchors.map((anchor) => anchor.getAttribute("href") ?? ""),
      );
    expect(
      links.some((href) => href.includes("admin") || href.includes("research")),
    ).toBe(false);
    await expect(
      page.getByText(
        /\bresearch\b|\badmin(?:istration)?\b|platform operations/i,
      ),
    ).toHaveCount(0);
  }
});

test("the authenticated shell stays navigable without horizontal overflow", async ({
  page,
}, testInfo) => {
  await grantTestSession(page);
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
