import { describe, expect, it } from "vitest";

import { safeAdminNextPath } from "@/lib/admin-navigation";

describe("admin login return paths", () => {
  it("keeps allowlisted admin routes on this host", () => {
    expect(safeAdminNextPath("/")).toBe("/");
    expect(safeAdminNextPath("/tenants/42?tab=sync")).toBe(
      "/tenants/42?tab=sync",
    );
    expect(safeAdminNextPath("/onboarding/new")).toBe("/onboarding/new");
  });

  it("rejects protocol-relative and off-site return paths", () => {
    expect(safeAdminNextPath("/.//evil.example")).toBeNull();
    expect(safeAdminNextPath("//evil.example")).toBeNull();
    expect(safeAdminNextPath("/\\evil.example")).toBeNull();
    expect(safeAdminNextPath("/\t/evil.example")).toBeNull();
    expect(safeAdminNextPath("https://evil.example/tenants")).toBeNull();
    expect(safeAdminNextPath("/tenants/../../evil.example")).toBeNull();
    expect(safeAdminNextPath("/onboarding-evil")).toBeNull();
  });
});
