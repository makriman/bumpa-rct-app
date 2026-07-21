import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import {
  apiPath,
  fulfillSession,
  grantTestSession,
  json,
  mockBumpa,
} from "./support";

async function expectWcagAA(page: Page) {
  const result = await new AxeBuilder({ page })
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
    result.violations,
    result.violations
      .map(
        (violation) =>
          `${violation.id}: ${violation.help} (${violation.nodes.length} nodes)`,
      )
      .join("\n"),
  ).toEqual([]);
}

async function mockConsumerRouteData(page: Page) {
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    if (
      path.includes("/api/backend/chat/conversations/") &&
      path.includes("/messages")
    ) {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    if (path === "/api/backend/tenants/current") {
      await json(route, {
        id: "tenant-e2e",
        slug: "e2e-store",
        name: "E2E Store",
        status: "active",
        business_category: "Retail",
        country: "NG",
        city: "Lagos",
        timezone: "Africa/Lagos",
        currency_code: "NGN",
        role: "owner",
      });
      return;
    }
    if (
      path === "/api/backend/settings/team" ||
      path === "/api/backend/settings/whatsapp-numbers" ||
      path === "/api/backend/mcp/registry" ||
      path === "/api/backend/settings/mcp-connections" ||
      path === "/api/backend/bumpa/sync-runs"
    ) {
      await json(route, []);
      return;
    }
    if (path === "/api/backend/settings/bumpa") {
      await json(route, {
        status: "active",
        provider: "bumpa",
        scope_type: "business_id",
        scope_id_last4: "7712",
        last_successful_sync_at: "2026-07-21T08:00:00Z",
        last_error: null,
      });
      return;
    }
    await route.abort("failed");
  });
}

type ChatVisualFixture = {
  populated?: boolean;
  onSend?: (route: Route) => Promise<void>;
};

async function mockChatVisualData(
  page: Page,
  { populated = false, onSend }: ChatVisualFixture = {},
) {
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path === "/api/backend/chat/conversations/page?limit=30") {
      await json(route, {
        items: populated
          ? [
              {
                id: "conversation-visual",
                title: "Weekly business review",
                updated_at: "2026-07-21T08:00:00Z",
                channel: "web",
                last_message_preview:
                  "Focus on fast-moving products and overdue follow-ups.",
              },
            ]
          : [],
        next_cursor: null,
      });
      return;
    }
    if (
      path ===
      "/api/backend/chat/conversations/conversation-visual/messages?limit=50"
    ) {
      await json(route, {
        items: [
          {
            id: "visual-question",
            direction: "inbound",
            content: "What should I focus on this week?",
            created_at: "2026-07-21T08:00:00Z",
          },
          {
            id: "visual-answer",
            direction: "outbound",
            content:
              "Prioritise your fastest-moving products, then follow up with customers whose orders are overdue.",
            created_at: "2026-07-21T08:01:00Z",
          },
        ],
        next_cursor: null,
      });
      return;
    }
    if (path === "/api/backend/chat/web" && onSend) {
      await onSend(route);
      return;
    }
    await route.abort("failed");
  });
}

const consumerRouteMatrix = [
  { path: "/", protected: false },
  { path: "/login", protected: false },
  { path: "/about", protected: false },
  { path: "/privacy", protected: false },
  { path: "/terms", protected: false },
  { path: "/chat", protected: true },
  { path: "/chat/conversation-e2e", protected: true },
  { path: "/profile", protected: true },
  { path: "/settings/team", protected: true },
  { path: "/settings/whatsapp", protected: true },
  { path: "/settings/bumpa", protected: true },
  { path: "/settings/mcp", protected: true },
];

function routeSnapshotName(path: string): string {
  const slug = path === "/" ? "landing" : path.slice(1).replaceAll("/", "-");
  return `route-${slug}.png`;
}

for (const route of consumerRouteMatrix) {
  test(`${route.path} passes the consumer route quality matrix`, async ({
    browserName,
    page,
  }) => {
    const browserProblems: string[] = [];
    page.on("pageerror", (error) => browserProblems.push(error.message));
    page.on("console", (message) => {
      const isAxeWebKitStyleProbe =
        browserName === "webkit" &&
        message.type() === "error" &&
        message
          .text()
          .startsWith("Refused to apply a stylesheet because its hash");
      if (isAxeWebKitStyleProbe) return;
      if (["error", "warning"].includes(message.type())) {
        browserProblems.push(`${message.type()}: ${message.text()}`);
      }
    });
    await mockConsumerRouteData(page);
    if (route.protected) await grantTestSession(page);
    const response = await page.goto(route.path);
    expect(response?.status()).toBe(200);
    await expect(page.locator("main")).toBeVisible();
    await page.waitForLoadState("networkidle");
    await settleVisuals(page);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await expectWcagAA(page);
    await settleDocumentGeometry(page);
    await expect(page).toHaveScreenshot(routeSnapshotName(route.path), {
      animations: "disabled",
      fullPage: true,
      maxDiffPixels: 200,
    });
    expect(browserProblems).toEqual([]);
  });
}

async function settleVisuals(page: Page) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await settleDocumentGeometry(page);
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

function cspDirective(policy: string, name: string): string {
  return (
    policy
      .split(";")
      .map((directive) => directive.trim())
      .find((directive) => directive.startsWith(`${name} `)) ?? ""
  );
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

  const countryPicker = page.getByRole("button", {
    name: "Country code, United Kingdom +44",
  });
  await countryPicker.click();
  await expect(
    page.getByRole("textbox", {
      name: "Search countries or calling codes",
    }),
  ).toBeFocused();
  await expectWcagAA(page);
});

test("login hydrates without locale-derived text mismatches", async ({
  page,
}) => {
  const hydrationErrors: string[] = [];
  const recordHydrationError = (message: string) => {
    if (
      message.includes("Hydration failed") ||
      message.includes("Minified React error #418")
    ) {
      hydrationErrors.push(message);
    }
  };
  page.on("pageerror", (error) => {
    recordHydrationError(error.message);
  });
  page.on("console", (message) => {
    if (message.type() === "error") recordHydrationError(message.text());
  });

  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await page.waitForLoadState("networkidle");

  expect(hydrationErrors).toEqual([]);
});

test("authenticated consumer settings have zero automated Axe violations and stay bounded", async ({
  page,
}) => {
  await grantTestSession(page);
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
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("public and authenticated documents enforce request-scoped nonce CSP", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const scope = window as typeof window & { __cspViolations?: string[] };
    scope.__cspViolations = [];
    window.addEventListener("securitypolicyviolation", (event) => {
      scope.__cspViolations?.push(
        `${event.effectiveDirective}:${event.blockedURI || "inline"}`,
      );
    });
  });
  await mockBumpa(page);

  const nonces: string[] = [];
  for (const path of ["/", "/settings/bumpa"]) {
    if (path !== "/") await grantTestSession(page);
    const response = await page.goto(path);
    expect(response).not.toBeNull();
    const headers = await response!.allHeaders();
    const policy = headers["content-security-policy"] ?? "";
    const scriptSource = cspDirective(policy, "script-src");
    const nonce = scriptSource.match(/'nonce-([^']+)'/)?.[1] ?? "";

    expect(nonce).toMatch(/^[A-Za-z0-9+/_-]{20,}={0,2}$/);
    expect(scriptSource).toContain("'strict-dynamic'");
    expect(scriptSource).not.toContain("'unsafe-inline'");
    expect(scriptSource).not.toContain("'unsafe-eval'");
    expect(cspDirective(policy, "script-src-attr")).toBe(
      "script-src-attr 'none'",
    );
    expect(cspDirective(policy, "style-src-attr")).toBe(
      "style-src-attr 'unsafe-inline'",
    );
    expect(headers["cache-control"]).toContain("no-store");
    expect(headers["x-nonce"]).toBeUndefined();

    const scriptNonces = await page
      .locator("script")
      .evaluateAll((scripts) =>
        scripts.map((script) => (script as HTMLScriptElement).nonce),
      );
    expect(scriptNonces.length).toBeGreaterThan(0);
    expect(scriptNonces.every((value) => value === nonce)).toBe(true);
    const styleNonces = await page
      .locator("style")
      .evaluateAll((styles) =>
        styles.map((style) => (style as HTMLStyleElement).nonce),
      );
    expect(styleNonces.every((value) => value === nonce)).toBe(true);
    expect(
      await page.evaluate(
        () =>
          (window as typeof window & { __cspViolations?: string[] })
            .__cspViolations ?? [],
      ),
    ).toEqual([]);
    nonces.push(nonce);
  }
  expect(new Set(nonces).size).toBe(nonces.length);
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
  await grantTestSession(page);
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

test("populated chat visual baseline does not regress", async ({ page }) => {
  await grantTestSession(page);
  await mockChatVisualData(page, { populated: true });
  await page.goto("/chat/conversation-visual");
  await expect(
    page.getByText(/Prioritise your fastest-moving products/),
  ).toBeVisible();
  await settleVisuals(page);
  await expectWcagAA(page);
  await expect(page).toHaveScreenshot("chat-populated.png", {
    animations: "disabled",
    maxDiffPixels: 200,
  });
});

test("sending chat visual baseline does not regress", async ({ page }) => {
  let releaseSend: () => void = () => undefined;
  const sendGate = new Promise<void>((resolve) => {
    releaseSend = resolve;
  });
  await grantTestSession(page);
  await mockChatVisualData(page, {
    onSend: async (route) => {
      await sendGate;
      await json(route, {
        answer: "Your stock priorities are ready.",
        conversation_id: "conversation-visual",
        inbound_message_id: "visual-inbound",
        outbound_message_id: "visual-outbound",
        data_freshness: null,
      });
    },
  });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "Message Bumpa Bestie" });
  await composer.fill("What should I restock first?");
  await expect(composer).toHaveValue("What should I restock first?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByLabel("Bumpa Bestie is thinking")).toBeVisible();
  try {
    await settleVisuals(page);
    await expectWcagAA(page);
    await expect(page).toHaveScreenshot("chat-sending.png", {
      animations: "disabled",
      maxDiffPixels: 200,
    });
  } finally {
    releaseSend();
  }
});

test("recoverable chat error visual baseline does not regress", async ({
  page,
}) => {
  await grantTestSession(page);
  await mockChatVisualData(page, {
    onSend: async (route) => {
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
    },
  });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "Message Bumpa Bestie" });
  await composer.fill("What changed this week?");
  await expect(composer).toHaveValue("What changed this week?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText("Your message was not sent.")).toBeVisible();
  await settleVisuals(page);
  await expectWcagAA(page);
  await expect(page).toHaveScreenshot("chat-error.png", {
    animations: "disabled",
    maxDiffPixels: 200,
  });
});

test("collapsed desktop chat visual baseline does not regress", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");
  await grantTestSession(page);
  await mockChatVisualData(page);
  await page.goto("/chat");
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(
    page.getByRole("button", { name: "Expand sidebar" }),
  ).toBeVisible();
  await settleVisuals(page);
  await expectWcagAA(page);
  await expect(page).toHaveScreenshot("chat-desktop-collapsed.png", {
    animations: "disabled",
    maxDiffPixels: 200,
  });
});

test("mobile chat drawer visual baseline does not regress", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium");
  await grantTestSession(page);
  await mockChatVisualData(page, { populated: true });
  await page.goto("/chat");
  await page.getByRole("button", { name: "Open conversation history" }).click();
  await expect(
    page.getByRole("dialog", { name: "Conversation history" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close conversation history" }),
  ).toBeFocused();
  await expectWcagAA(page);
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("chat-mobile-drawer.png", {
    animations: "disabled",
    maxDiffPixels: 200,
  });
});

test("chat account menu visual baseline does not regress", async ({
  page,
}, testInfo) => {
  await grantTestSession(page);
  await mockChatVisualData(page);
  await page.goto("/chat");
  if (testInfo.project.name === "mobile-chromium") {
    await page
      .getByRole("button", { name: "Open conversation history" })
      .click();
  }
  await page.getByText("Your account", { exact: true }).click();
  await expect(page.getByRole("link", { name: "Profile" })).toBeVisible();
  await expectWcagAA(page);
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("chat-account-menu.png", {
    animations: "disabled",
    maxDiffPixels: 200,
  });
});
