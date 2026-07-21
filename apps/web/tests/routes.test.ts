import { existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const consumerRoutes = [
  "app/page.tsx",
  "app/about/page.tsx",
  "app/privacy/page.tsx",
  "app/terms/page.tsx",
  "app/login/page.tsx",
  "app/chat/page.tsx",
  "app/chat/[conversationId]/page.tsx",
  "app/profile/page.tsx",
  "app/settings/team/page.tsx",
  "app/settings/whatsapp/page.tsx",
  "app/settings/bumpa/page.tsx",
  "app/settings/mcp/page.tsx",
];

describe("consumer route contract", () => {
  it.each(consumerRoutes)("includes %s", (route) => {
    expect(existsSync(path.resolve(process.cwd(), route))).toBe(true);
  });

  it("does not compile privileged product routes", () => {
    expect(existsSync(path.resolve(process.cwd(), "app/admin/page.tsx"))).toBe(
      false,
    );
    expect(
      existsSync(path.resolve(process.cwd(), "app/research/page.tsx")),
    ).toBe(false);
    expect(
      existsSync(path.resolve(process.cwd(), "app/research-consent/page.tsx")),
    ).toBe(false);
  });

  it("includes the container health endpoint", () => {
    expect(
      existsSync(path.resolve(process.cwd(), "app/api/health/route.ts")),
    ).toBe(true);
  });
});
