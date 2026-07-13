import { describe, expect, it } from "vitest";
import { existsSync } from "node:fs";
import path from "node:path";

const routes = [
  "app/page.tsx",
  "app/about/page.tsx",
  "app/privacy/page.tsx",
  "app/terms/page.tsx",
  "app/research-consent/page.tsx",
  "app/login/page.tsx",
  "app/chat/page.tsx",
  "app/profile/page.tsx",
  "app/settings/team/page.tsx",
  "app/settings/whatsapp/page.tsx",
  "app/settings/bumpa/page.tsx",
  "app/settings/mcp/page.tsx",
  "app/admin/page.tsx",
  "app/admin/tenants/page.tsx",
  "app/admin/onboarding/page.tsx",
  "app/admin/onboarding/[id]/page.tsx",
  "app/admin/connections/page.tsx",
  "app/admin/users/page.tsx",
  "app/admin/sync/page.tsx",
  "app/admin/errors/page.tsx",
  "app/admin/providers/page.tsx",
  "app/admin/usage/page.tsx",
  "app/research/page.tsx",
  "app/research/questions/page.tsx",
  "app/research/conversations/page.tsx",
  "app/research/classifications/page.tsx",
  "app/research/cohorts/page.tsx",
  "app/research/reports/page.tsx",
  "app/research/exports/page.tsx",
];

describe("required route contract", () => {
  it.each(routes)("includes %s", (route) =>
    expect(existsSync(path.resolve(process.cwd(), route))).toBe(true),
  );
  it("includes the container health endpoint", () =>
    expect(
      existsSync(path.resolve(process.cwd(), "app/api/health/route.ts")),
    ).toBe(true));
});
