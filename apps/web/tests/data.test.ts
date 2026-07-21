import { describe, expect, it } from "vitest";

import { statusTone, workspaceRoleLabel } from "@/lib/consumer-data";
import { safeConsumerNextPath } from "@/lib/consumer-navigation";
import { userNav } from "@/lib/navigation";

describe("consumer product rules", () => {
  it.each([
    ["Connected", "success"],
    ["Partial", "warning"],
    ["Failed", "danger"],
    ["WhatsApp", "info"],
  ])("maps %s to %s", (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  });

  it("contains only consumer destinations", () => {
    expect(
      userNav.flatMap((group) => group.items.map((item) => item.href)),
    ).toEqual([
      "/chat",
      "/profile",
      "/settings/team",
      "/settings/whatsapp",
      "/settings/bumpa",
      "/settings/mcp",
    ]);
  });

  it("uses consumer workspace language for the API manager role", () => {
    expect(workspaceRoleLabel("admin")).toBe("Manager");
    expect(workspaceRoleLabel("owner")).toBe("Owner");
    expect(workspaceRoleLabel(null)).toBe("Member");
  });

  it("accepts only consumer-local login return paths", () => {
    expect(safeConsumerNextPath("/chat/thread-1?from=history#latest")).toBe(
      "/chat/thread-1?from=history#latest",
    );
    expect(safeConsumerNextPath("/settings/team")).toBe("/settings/team");
    expect(safeConsumerNextPath("https://admin.bumpabestie.com/tenants")).toBe(
      null,
    );
    expect(safeConsumerNextPath("//research.bumpabestie.com/questions")).toBe(
      null,
    );
    expect(safeConsumerNextPath("/chat/../admin/tenants")).toBeNull();
    expect(safeConsumerNextPath("/chat\\redirect")).toBeNull();
  });
});
