import { afterEach, describe, expect, it, vi } from "vitest";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import {
  buildContentSecurityPolicy,
  CONTENT_SECURITY_POLICY_HEADER,
} from "@/lib/content-security-policy";
import { config, middleware } from "@/middleware";

function cspDirective(policy: string, name: string): string {
  return (
    policy
      .split(";")
      .map((directive) => directive.trim())
      .find((directive) => directive.startsWith(`${name} `)) ?? ""
  );
}

function expectStrictDocumentResponse(response: Response): string {
  const policy = response.headers.get(CONTENT_SECURITY_POLICY_HEADER) ?? "";
  const scriptSource = cspDirective(policy, "script-src");
  expect(scriptSource).toContain("'strict-dynamic'");
  expect(scriptSource).not.toContain("'unsafe-inline'");
  expect(cspDirective(policy, "script-src-attr")).toBe(
    "script-src-attr 'none'",
  );
  expect(cspDirective(policy, "style-src-attr")).toBe(
    "style-src-attr 'unsafe-inline'",
  );
  expect(response.headers.get("cache-control")).toContain("no-store");
  expect(response.headers.get("x-nonce")).toBeNull();
  const nonce = scriptSource.match(/'nonce-([^']+)'/)?.[1] ?? "";
  expect(nonce).toMatch(/^[A-Za-z0-9+/_-]{20,}={0,2}$/);
  return nonce;
}

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
      expectStrictDocumentResponse(response);
      expect(response.headers.get("x-robots-tag")).toBe(
        "noindex, nofollow, noarchive",
      );
    },
  );

  it("indexes the public home but prevents discovery of authentication and private surfaces", async () => {
    const home = await middleware(
      new NextRequest("https://bumpabestie.com/", {
        headers: { host: "bumpabestie.com" },
      }),
    );
    const login = await middleware(
      new NextRequest("https://bumpabestie.com/login", {
        headers: { host: "bumpabestie.com" },
      }),
    );
    const consent = await middleware(
      new NextRequest("https://bumpabestie.com/research-consent", {
        headers: { host: "bumpabestie.com" },
      }),
    );

    expect(home.headers.get("x-robots-tag")).toBeNull();
    expect(consent.headers.get("x-robots-tag")).toBeNull();
    expect(login.headers.get("x-robots-tag")).toBe(
      "noindex, nofollow, noarchive",
    );
  });

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
    expectStrictDocumentResponse(response);
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
    expectStrictDocumentResponse(response);
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
    expectStrictDocumentResponse(response);
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
    expectStrictDocumentResponse(response);
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
    expectStrictDocumentResponse(response);
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
    expectStrictDocumentResponse(response);
  });

  it("builds a production policy with nonce-gated scripts and only a scoped style exception", () => {
    const policy = buildContentSecurityPolicy("unit-test-nonce", false);

    expect(cspDirective(policy, "script-src")).toBe(
      "script-src 'self' 'nonce-unit-test-nonce' 'strict-dynamic'",
    );
    expect(cspDirective(policy, "script-src")).not.toContain("unsafe");
    expect(cspDirective(policy, "style-src")).toBe(
      "style-src 'self' 'nonce-unit-test-nonce'",
    );
    expect(cspDirective(policy, "style-src-attr")).toBe(
      "style-src-attr 'unsafe-inline'",
    );
    expect(policy).not.toContain("'unsafe-eval'");
  });

  it("replaces spoofed policy and nonce headers with a fresh request policy", async () => {
    const attackerPolicy =
      "script-src 'unsafe-inline' https://attacker.invalid";
    const first = await middleware(
      new NextRequest("https://bumpabestie.com/login", {
        headers: {
          host: "bumpabestie.com",
          "content-security-policy": attackerPolicy,
          "content-security-policy-report-only": attackerPolicy,
          "x-nonce": "attacker-controlled",
        },
      }),
    );
    const second = await middleware(
      new NextRequest("https://bumpabestie.com/login", {
        headers: { host: "bumpabestie.com" },
      }),
    );

    const firstNonce = expectStrictDocumentResponse(first);
    const secondNonce = expectStrictDocumentResponse(second);
    expect(first.headers.get(CONTENT_SECURITY_POLICY_HEADER)).not.toContain(
      "attacker.invalid",
    );
    expect(firstNonce).not.toBe("attacker-controlled");
    expect(secondNonce).not.toBe(firstNonce);
  });

  it("matches documents and protected RSC prefetches but not non-document resources", () => {
    const matches = (url: string, headers?: Record<string, string>) =>
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url,
        headers,
      });

    expect(matches("/chat")).toBe(true);
    expect(
      matches("/chat", {
        RSC: "1",
        "Next-Router-Prefetch": "1",
        "Next-Router-Segment-Prefetch": "/chat",
      }),
    ).toBe(true);
    expect(matches("/api/health")).toBe(false);
    expect(matches("/_next/static/chunks/app.js")).toBe(false);
    expect(matches("/brand/social-card.png")).toBe(false);
    expect(matches("/brand/app-icon-192.png")).toBe(false);
    expect(matches("/brand-mark.svg")).toBe(false);
    expect(matches("/favicon.ico")).toBe(false);
    expect(matches("/icon.svg")).toBe(false);
    expect(matches("/apple-icon.png")).toBe(false);
    expect(matches("/manifest.webmanifest")).toBe(false);
    expect(matches("/robots.txt")).toBe(false);
    expect(matches("/sitemap.xml")).toBe(false);
  });
});
