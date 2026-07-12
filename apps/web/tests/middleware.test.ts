import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";

describe("host routing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_DEMO_MODE;
  });

  it.each(["admin.bumpabestie.com", "research.bumpabestie.com"])(
    "keeps /login public on %s",
    async (host) => {
      const response = await middleware(
        new NextRequest("https://bumpabestie.com/login", { headers: { host } }),
      );
      expect(response.headers.get("x-middleware-rewrite")).toBeNull();
      expect(response.headers.get("location")).toBeNull();
    },
  );

  it("rewrites the admin host root to the admin surface in explicit demo mode", async () => {
    const previous = process.env.NEXT_PUBLIC_DEMO_MODE;
    process.env.NEXT_PUBLIC_DEMO_MODE = "true";
    const response = await middleware(
      new NextRequest("https://bumpabestie.com/", {
        headers: {
          host: "admin.bumpabestie.com",
          cookie: "bb_session=demo",
        },
      }),
    );
    if (previous === undefined) delete process.env.NEXT_PUBLIC_DEMO_MODE;
    else process.env.NEXT_PUBLIC_DEMO_MODE = previous;
    expect(response.headers.get("x-middleware-rewrite")).toContain("/admin");
  });

  it("rejects a tenant-only session from the admin surface", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            platform_roles: [],
            memberships: [{ role: "owner", status: "active" }],
          }),
          { status: 200 },
        ),
      ),
    );
    const response = await middleware(
      new NextRequest("https://admin.bumpabestie.com/admin", {
        headers: {
          host: "admin.bumpabestie.com",
          cookie: "bb_session=signed-token",
        },
      }),
    );
    expect(response.headers.get("location")).toContain("/login");
  });

  it("allows an operator session onto the admin surface", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ platform_roles: ["operator"] }), {
          status: 200,
        }),
      ),
    );
    const response = await middleware(
      new NextRequest("https://admin.bumpabestie.com/admin", {
        headers: {
          host: "admin.bumpabestie.com",
          cookie: "bb_session=signed-token",
        },
      }),
    );
    expect(response.headers.get("location")).toBeNull();
  });

  it("rejects an SME owner from the research surface", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            platform_roles: [],
            memberships: [{ role: "owner", status: "active" }],
          }),
          { status: 200 },
        ),
      ),
    );
    const response = await middleware(
      new NextRequest("https://research.bumpabestie.com/research/questions", {
        headers: {
          host: "research.bumpabestie.com",
          cookie: "bb_session=signed-token",
        },
      }),
    );
    expect(response.headers.get("location")).toContain("/login");
  });

  it("allows a researcher onto the research surface", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ platform_roles: ["researcher"] }), {
          status: 200,
        }),
      ),
    );
    const response = await middleware(
      new NextRequest("https://research.bumpabestie.com/research/reports", {
        headers: {
          host: "research.bumpabestie.com",
          cookie: "bb_session=signed-token",
        },
      }),
    );
    expect(response.headers.get("location")).toBeNull();
  });

  it("requires an active tenant membership on the user surface", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            platform_roles: [],
            memberships: [{ role: "owner", status: "disabled" }],
          }),
          { status: 200 },
        ),
      ),
    );
    const response = await middleware(
      new NextRequest("https://bumpabestie.com/chat", {
        headers: {
          host: "bumpabestie.com",
          cookie: "bb_session=signed-token",
        },
      }),
    );
    expect(response.headers.get("location")).toContain("/login?next=%2Fchat");
  });
});
