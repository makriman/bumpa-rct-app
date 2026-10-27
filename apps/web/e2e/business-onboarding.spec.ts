import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import initialProfile from "../tests/fixtures/business-profile.json";
import completedProfile from "../tests/fixtures/business-profile-completed.json";
import { apiPath, fulfillSession, grantTestSession, json } from "./support";

for (const simulateFailure of [false, true]) {
  test(`business setup supports review, corrections and reconnects${simulateFailure ? " with a failed save" : ""}`, async ({
    page,
  }, testInfo) => {
    const profile = structuredClone(initialProfile);
    let failedOnce = false;
    await grantTestSession(page);
    await page.route("**/api/backend/**", async (route) => {
      const path = apiPath(route);
      if (await fulfillSession(route)) return;
      if (path.startsWith("/api/backend/chat/conversations")) {
        await json(route, { items: [], next_cursor: null });
        return;
      }
      if (path === "/api/backend/settings/business-profile") {
        if (route.request().method() === "POST") {
          const command = route.request().postDataJSON();
          expect(command.expected_revision).toBe(profile.revision);
          if (simulateFailure && command.field === "customers" && !failedOnce) {
            failedOnce = true;
            await json(
              route,
              { detail: "Connection interrupted. Please try again." },
              503,
            );
            return;
          }
          profile.revision++;
          if (command.action === "start" || command.action === "resume") {
            profile.status = "in_progress";
            profile.step =
              profile.questions.find((q) => !(q.key in profile.draft))?.key ??
              "review";
          } else if (command.action === "answer") {
            Object.assign(profile.draft, {
              [command.field]: command.detail
                ? { value: command.value, detail: command.detail }
                : command.value,
            });
            profile.step =
              profile.questions.find((q) => !(q.key in profile.draft))?.key ??
              "review";
            if (profile.step === "review") profile.status = "review";
          } else if (command.action === "edit") {
            profile.step = command.field;
            profile.status = "in_progress";
          } else if (command.action === "pause") {
            profile.status = "paused";
          } else if (command.action === "complete") {
            profile.status = "completed";
            profile.answers = { ...profile.draft };
            profile.draft = {};
            profile.suggestions = completedProfile.suggestions;
          }
        }
        await json(route, profile);
        return;
      }
      await route.abort("failed");
    });
    await page.goto("/chat");
    await expect(
      page.getByRole("heading", { name: /Big plans start/ }),
    ).toBeVisible();
    if (!simulateFailure)
      await page.screenshot({
        path: testInfo.outputPath("onboarding-welcome.png"),
        fullPage: true,
      });
    await page.getByRole("button", { name: "Let’s start →" }).click();
    await expect(
      page.getByRole("heading", { name: "What do you sell or do?" }),
    ).toBeFocused();
    await page
      .getByRole("textbox", { name: "What do you sell or do?" })
      .fill("Women’s shoes and repairs");
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Who usually buys from you?" }),
    ).toBeFocused();
    await page
      .getByRole("textbox", { name: "Who usually buys from you?" })
      .fill("Working women in Ibadan");
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    if (simulateFailure) {
      await expect(page.locator(".onboarding-error")).toContainText(
        "Connection interrupted",
      );
      await expect(
        page.getByRole("textbox", { name: "Who usually buys from you?" }),
      ).toHaveValue("Working women in Ibadan");
      await page
        .getByRole("button", { name: "Continue →", exact: true })
        .click();
    }
    await page.getByRole("checkbox", { name: "WhatsApp", exact: true }).check();
    await page
      .getByRole("checkbox", { name: "Physical shop", exact: true })
      .check();
    await page.getByRole("checkbox", { name: "Other", exact: true }).check();
    await page
      .getByRole("textbox", { name: "Tell me a little more" })
      .fill("Market stall");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    if (!simulateFailure)
      await page.screenshot({
        path: testInfo.outputPath("onboarding-channels.png"),
        fullPage: true,
      });
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await page
      .getByRole("radio", { name: "It varies a lot / I’m not sure" })
      .check();
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await page.getByRole("button", { name: "Finish later" }).click();
    await page.reload();
    await page.getByRole("button", { name: "Continue my setup →" }).click();
    await expect(page.getByText("Question 5 of 7")).toBeVisible();
    await page.getByRole("radio", { name: "2–3 people" }).check();
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await page
      .getByRole("radio", { name: "Get more customers", exact: true })
      .check();
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await page
      .getByRole("textbox", {
        name: "What’s making that difficult at the moment?",
      })
      .fill("Delivery costs put people off ordering");
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "Does this sound like your business?",
      }),
    ).toBeVisible();
    await expect(
      page.getByText("WhatsApp, Physical shop, Other: Market stall"),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Change people in your business" })
      .click();
    await page.getByRole("radio", { name: "Just me (1 person)" }).check();
    await page.getByRole("button", { name: "Continue →", exact: true }).click();
    await expect(
      page.getByText("Just me (1 person)", { exact: true }),
    ).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    if (!simulateFailure)
      await page.screenshot({
        path: testInfo.outputPath("onboarding-review.png"),
        fullPage: true,
      });
    await page
      .getByRole("button", { name: "Yes, save my business profile →" })
      .click();
    await expect(
      page.getByRole("heading", { name: "I’ve got you, Bestie." }),
    ).toBeVisible();
    expect(Object.keys(profile.answers)).toHaveLength(7);
    const firstUsefulQuestion = page.getByRole("button", {
      name: completedProfile.suggestions[0],
    });
    await expect(firstUsefulQuestion).toBeInViewport({ ratio: 1 });
    const savedDetails = page.locator(".onboarding-saved-details");
    await expect(savedDetails).not.toHaveAttribute("open");
    await savedDetails.getByText("Review my saved business details").click();
    await expect(
      savedDetails.getByText("Just me (1 person)", { exact: true }),
    ).toBeVisible();
    await savedDetails.getByText("Review my saved business details").click();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    if (!simulateFailure)
      await page.screenshot({
        path: testInfo.outputPath("onboarding-saved.png"),
        fullPage: true,
      });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Ask my own question →" }).click();
    await expect(
      page.getByRole("textbox", { name: "Message Bumpa Bestie" }),
    ).toBeVisible();
  });
}

test("a delayed business profile keeps focus inside the open mobile drawer", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium");
  let finishProfile = () => {};
  const profileReady = new Promise<void>((resolve) => {
    finishProfile = resolve;
  });
  await grantTestSession(page);
  await page.route("**/api/backend/**", async (route) => {
    const path = apiPath(route);
    if (await fulfillSession(route)) return;
    if (path.startsWith("/api/backend/chat/conversations")) {
      await json(route, { items: [], next_cursor: null });
      return;
    }
    if (path === "/api/backend/settings/business-profile") {
      await profileReady;
      await json(route, completedProfile);
      return;
    }
    await route.abort("failed");
  });
  try {
    await page.goto("/chat");
    await expect(
      page.getByRole("status").filter({
        hasText: "Getting your business profile ready…",
      }),
    ).toBeVisible();
    const opener = page.getByRole("button", {
      name: "Open conversation history",
    });
    await opener.click();
    const close = page.getByRole("button", {
      name: "Close conversation history",
    });
    await expect(close).toBeFocused();
    finishProfile();
    await expect(
      page.getByRole("heading", { name: "What shall we work on today?" }),
    ).toBeVisible();
    await expect(close).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(
      page.getByRole("link", { name: "Bumpa Bestie chat home" }),
    ).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(
      page.getByRole("button", { name: "Collapse sidebar" }),
    ).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(
      page.getByRole("link", { name: "Bumpa Bestie chat home" }),
    ).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(opener).toBeFocused();
  } finally {
    finishProfile();
  }
});
