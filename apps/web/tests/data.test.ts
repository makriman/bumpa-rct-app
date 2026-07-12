import { describe, expect, it } from "vitest";
import { statusTone } from "@/lib/demo-data";
import { homeForRole, safeNextPath } from "@/lib/navigation";

describe("product rules", () => {
  it.each([
    ["Connected", "success"],
    ["Partial", "warning"],
    ["Failed", "danger"],
    ["WhatsApp", "info"],
  ])("maps %s to %s", (status, tone) => expect(statusTone(status)).toBe(tone));

  it("routes demo identities to the correct isolated surface", () => {
    expect(homeForRole("owner")).toBe("/chat");
    expect(homeForRole("operator")).toBe("/admin");
    expect(homeForRole("researcher")).toBe("/research");
  });

  it("allows only local post-authentication destinations", () => {
    expect(safeNextPath("/settings/bumpa?from=login")).toBe(
      "/settings/bumpa?from=login",
    );
    expect(safeNextPath("https://attacker.example/steal")).toBeNull();
    expect(safeNextPath("//attacker.example/steal")).toBeNull();
    expect(safeNextPath("/\\attacker.example/steal")).toBeNull();
  });
});
