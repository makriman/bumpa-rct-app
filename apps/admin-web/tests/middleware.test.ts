import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CONTENT_SECURITY_POLICY_HEADER } from "@/lib/content-security-policy";
import { config, middleware } from "@/middleware";

function expectPrivateBoundary(response: Response) {
  const policy = response.headers.get(CONTENT_SECURITY_POLICY_HEADER) ?? "";
  const scripts =
    policy
      .split(";")
      .map((value) => value.trim())
      .find((value) => value.startsWith("script-src ")) ?? "";
  expect(scripts).toContain("'strict-dynamic'");
  expect(scripts).not.toContain("'unsafe-inline'");
  expect(response.headers.get("cache-control")).toContain("no-store");
  expect(response.headers.get("x-robots-tag")).toBe(
    "noindex, nofollow, noarchive",
  );
}

describe("admin request boundary", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_DEMO_MODE;
  });

  it("fails closed without a host-scoped session", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const response = await middleware(
      new NextRequest("https://admin.bumpabestie.com/tenants"),
    );
    expect(response.headers.get("location")).toBe(
      "https://admin.bumpabestie.com/login?next=%2Ftenants",
    );
    expectPrivateBoundary(response);
  });

  it("never treats a public demo flag as an authentication bypass", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "true";
    const response = await middleware(
      new NextRequest("https://admin.bumpabestie.com/tenants"),
    );
    expect(response.headers.get("location")).toContain("/login?next=");
  });

  it("rejects a research-only identity and accepts an operator", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ platform_roles: ["researcher"] })),
    );
    const rejected = await middleware(
      new NextRequest("https://admin.bumpabestie.com/", {
        headers: { cookie: "bb_session=host-session" },
      }),
    );
    expect(rejected.headers.get("location")).toContain("/login?next=%2F");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ platform_roles: ["operator"] })),
    );
    const allowed = await middleware(
      new NextRequest("https://admin.bumpabestie.com/", {
        headers: { cookie: "bb_session=host-session" },
      }),
    );
    expect(allowed.headers.get("location")).toBeNull();
    expectPrivateBoundary(allowed);
  });

  it("permanently redirects legacy routes without reviving old pages", async () => {
    const response = await middleware(
      new NextRequest("https://admin.bumpabestie.com/admin/tenants"),
    );
    expect(response.status).toBe(308);
    expect(response.headers.get("location")).toBe(
      "https://admin.bumpabestie.com/tenants",
    );

    for (const [legacy, current] of [
      ["/admin/errors", "/failures"],
      ["/admin/providers", "/provider-failures"],
      ["/admin/sync", "/sync-runs"],
      ["/admin/users", "/administrators"],
    ]) {
      const mapped = await middleware(
        new NextRequest(`https://admin.bumpabestie.com${legacy}`),
      );
      expect(mapped.status).toBe(308);
      expect(new URL(mapped.headers.get("location") ?? "").pathname).toBe(
        current,
      );
    }
  });

  it("preserves a protected-route query in the validated login return path", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const response = await middleware(
      new NextRequest(
        "https://admin.bumpabestie.com/provider-failures?severity=high",
      ),
    );
    expect(response.headers.get("location")).toBe(
      "https://admin.bumpabestie.com/login?next=%2Fprovider-failures%3Fseverity%3Dhigh",
    );
  });

  it("matches documents and excludes static or API resources", () => {
    const matches = (url: string) =>
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url });
    expect(matches("/tenants")).toBe(true);
    expect(matches("/api/health")).toBe(false);
    expect(matches("/_next/static/app.js")).toBe(false);
    expect(matches("/brand/logo.svg")).toBe(false);
  });
});
