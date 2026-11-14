import { describe, expect, it } from "vitest";

import { safeResearchNextPath } from "@/lib/research-navigation";

describe("research login return paths", () => {
  it("keeps allowlisted research routes on this host", () => {
    expect(safeResearchNextPath("/")).toBe("/");
    expect(safeResearchNextPath("/questions?intent=finance")).toBe(
      "/questions?intent=finance",
    );
    expect(safeResearchNextPath("/exports/daily")).toBe("/exports/daily");
  });

  it("rejects protocol-relative and off-site return paths", () => {
    expect(safeResearchNextPath("/.//evil.example")).toBeNull();
    expect(safeResearchNextPath("//evil.example")).toBeNull();
    expect(safeResearchNextPath("/\\evil.example")).toBeNull();
    expect(safeResearchNextPath("/\t/evil.example")).toBeNull();
    expect(safeResearchNextPath("https://admin.example/questions")).toBeNull();
    expect(safeResearchNextPath("/questions/../../evil.example")).toBeNull();
  });
});
