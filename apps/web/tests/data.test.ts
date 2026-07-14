import { describe, expect, it } from "vitest";
import { statusTone } from "@/lib/demo-data";
import {
  homeForRole,
  resolvePostLoginDestination,
  safeNextPath,
} from "@/lib/navigation";

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

  it.each([
    ["admin.bumpabestie.com", "admin"],
    ["research.bumpabestie.com", "research"],
  ])(
    "keeps an owner signed in but denies the unauthorized %s surface",
    (hostname, surface) => {
      expect(
        resolvePostLoginDestination({
          hostname,
          next: null,
          platformRoles: [],
          memberships: [{ role: "owner", status: "active" }],
        }),
      ).toEqual({ authorized: false, surface });
    },
  );

  it.each([
    ["admin.bumpabestie.com", ["operator"], "/admin"],
    ["admin.bumpabestie.com", ["superadmin"], "/admin"],
    ["research.bumpabestie.com", ["researcher"], "/research"],
    ["research.bumpabestie.com", ["superadmin"], "/research"],
  ])("allows an authorized role on %s", (hostname, platformRoles, path) => {
    expect(
      resolvePostLoginDestination({
        hostname,
        next: null,
        platformRoles,
        memberships: [],
      }),
    ).toEqual({ authorized: true, path });
  });

  it("validates protected next paths against the authenticated session", () => {
    const owner = {
      hostname: "bumpabestie.com",
      platformRoles: [] as string[],
      memberships: [{ role: "owner", status: "active" }],
    };
    expect(
      resolvePostLoginDestination({ ...owner, next: "/admin/tenants" }),
    ).toEqual({ authorized: false, surface: "admin" });
    expect(
      resolvePostLoginDestination({
        ...owner,
        next: "/research/questions",
      }),
    ).toEqual({ authorized: false, surface: "research" });
    expect(
      resolvePostLoginDestination({ ...owner, next: "/settings/team" }),
    ).toEqual({ authorized: true, path: "/settings/team" });
    expect(
      resolvePostLoginDestination({
        hostname: "bumpabestie.com",
        next: "/admin/users?from=login",
        platformRoles: ["operator"],
        memberships: [],
      }),
    ).toEqual({ authorized: true, path: "/admin/users?from=login" });
    expect(
      resolvePostLoginDestination({
        hostname: "bumpabestie.com",
        next: "/research/reports",
        platformRoles: ["researcher"],
        memberships: [],
      }),
    ).toEqual({ authorized: true, path: "/research/reports" });
  });

  it("uses the active workspace first on the public host and the branded host for dual-role sessions", () => {
    const dualRole = {
      platformRoles: ["operator", "researcher"],
      memberships: [{ role: "owner", status: "active" }],
    };
    expect(
      resolvePostLoginDestination({
        hostname: "admin.bumpabestie.com",
        next: null,
        ...dualRole,
      }),
    ).toEqual({ authorized: true, path: "/admin" });
    expect(
      resolvePostLoginDestination({
        hostname: "research.bumpabestie.com",
        next: null,
        ...dualRole,
      }),
    ).toEqual({ authorized: true, path: "/research" });
    expect(
      resolvePostLoginDestination({
        hostname: "bumpabestie.com",
        next: null,
        ...dualRole,
      }),
    ).toEqual({ authorized: true, path: "/chat" });
  });

  it("does not honor cross-surface next paths on branded hosts", () => {
    expect(
      resolvePostLoginDestination({
        hostname: "admin.bumpabestie.com",
        next: "/research/reports",
        platformRoles: ["operator"],
        memberships: [],
      }),
    ).toEqual({ authorized: true, path: "/admin" });
  });
});
