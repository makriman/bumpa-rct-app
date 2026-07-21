import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildContentSecurityPolicy,
  CONTENT_SECURITY_POLICY_HEADER,
} from "@/lib/content-security-policy";
import { config, middleware } from "@/middleware";

function directive(policy: string, name: string): string {
  return (
    policy
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(`${name} `)) ?? ""
  );
}

function expectStrictDocumentResponse(response: Response): string {
  const policy = response.headers.get(CONTENT_SECURITY_POLICY_HEADER) ?? "";
  const scripts = directive(policy, "script-src");
  expect(scripts).toContain("'strict-dynamic'");
  expect(scripts).not.toContain("'unsafe-inline'");
  expect(directive(policy, "script-src-attr")).toBe("script-src-attr 'none'");
  expect(response.headers.get("cache-control")).toContain("no-store");
  expect(response.headers.get("x-nonce")).toBeNull();
  const nonce = scripts.match(/'nonce-([^']+)'/)?.[1] ?? "";
  expect(nonce).toMatch(/^[A-Za-z0-9+/_-]{20,}={0,2}$/);
  return nonce;
}

describe("consumer request boundary", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_DEMO_MODE;
  });

  it("indexes the public home and prevents indexing private pages", async () => {
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
    expect(home.headers.get("x-robots-tag")).toBeNull();
    expect(login.headers.get("x-robots-tag")).toBe(
      "noindex, nofollow, noarchive",
    );
    expectStrictDocumentResponse(home);
    expectStrictDocumentResponse(login);
  });

  it("requires an active consumer membership", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
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

  it("never treats a public demo flag as an authentication bypass", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "true";
    const response = await middleware(
      new NextRequest("https://bumpabestie.com/chat", {
        headers: { host: "bumpabestie.com" },
      }),
    );
    expect(response.headers.get("location")).toContain("/login?next=%2Fchat");
  });

  it("preserves a protected-route query in the validated login return path", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const response = await middleware(
      new NextRequest("https://bumpabestie.com/chat/thread-1?from=history", {
        headers: { host: "bumpabestie.com" },
      }),
    );
    expect(response.headers.get("location")).toBe(
      "https://bumpabestie.com/login?next=%2Fchat%2Fthread-1%3Ffrom%3Dhistory",
    );
  });

  it("allows an active consumer membership", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            memberships: [{ role: "member", status: "active" }],
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
    expect(response.headers.get("location")).toBeNull();
    expectStrictDocumentResponse(response);
  });

  it("replaces spoofed policy and nonce headers", async () => {
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

  it("builds a nonce-gated production policy", () => {
    const policy = buildContentSecurityPolicy("unit-test-nonce", false);
    expect(directive(policy, "script-src")).toBe(
      "script-src 'self' 'nonce-unit-test-nonce' 'strict-dynamic'",
    );
    expect(policy).not.toContain("'unsafe-eval'");
  });

  it("matches documents but skips static resources", () => {
    const matches = (url: string) =>
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url });
    expect(matches("/chat")).toBe(true);
    expect(matches("/api/health")).toBe(false);
    expect(matches("/_next/static/chunks/app.js")).toBe(false);
    expect(matches("/brand/social-card.png")).toBe(false);
    expect(matches("/robots.txt")).toBe(false);
  });
});
