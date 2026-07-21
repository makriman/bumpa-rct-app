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

describe("research request boundary", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_DEMO_MODE;
  });

  it("fails closed without a host-scoped session", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const response = await middleware(
      new NextRequest("https://research.bumpabestie.com/questions"),
    );
    expect(response.headers.get("location")).toBe(
      "https://research.bumpabestie.com/login?next=%2Fquestions",
    );
    expectPrivateBoundary(response);
  });

  it("never treats a public demo flag as an authentication bypass", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "true";
    const response = await middleware(
      new NextRequest("https://research.bumpabestie.com/questions"),
    );
    expect(response.headers.get("location")).toContain("/login?next=");
  });

  it("preserves a protected-route query in the validated login return path", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const response = await middleware(
      new NextRequest(
        "https://research.bumpabestie.com/questions?intent=finance",
      ),
    );
    expect(response.headers.get("location")).toBe(
      "https://research.bumpabestie.com/login?next=%2Fquestions%3Fintent%3Dfinance",
    );
  });

  it("rejects an operator-only identity and accepts a researcher", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ platform_roles: ["operator"] })),
    );
    const rejected = await middleware(
      new NextRequest("https://research.bumpabestie.com/", {
        headers: { cookie: "bb_session=host-session" },
      }),
    );
    expect(rejected.headers.get("location")).toContain("/login?next=%2F");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ platform_roles: ["researcher"] })),
    );
    const allowed = await middleware(
      new NextRequest("https://research.bumpabestie.com/", {
        headers: { cookie: "bb_session=host-session" },
      }),
    );
    expect(allowed.headers.get("location")).toBeNull();
    expectPrivateBoundary(allowed);
  });

  it("keeps consent public and redirects both legacy route shapes", async () => {
    process.env.NEXT_PUBLIC_DEMO_MODE = "false";
    const consent = await middleware(
      new NextRequest("https://research.bumpabestie.com/consent"),
    );
    expect(consent.headers.get("location")).toBeNull();
    expectPrivateBoundary(consent);

    const oldQuestion = await middleware(
      new NextRequest("https://research.bumpabestie.com/research/questions"),
    );
    expect(oldQuestion.status).toBe(308);
    expect(oldQuestion.headers.get("location")).toBe(
      "https://research.bumpabestie.com/questions",
    );

    const oldConsent = await middleware(
      new NextRequest("https://research.bumpabestie.com/research-consent"),
    );
    expect(oldConsent.status).toBe(308);
    expect(oldConsent.headers.get("location")).toBe(
      "https://research.bumpabestie.com/consent",
    );
  });

  it("matches documents and excludes static or API resources", () => {
    const matches = (url: string) =>
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url });
    expect(matches("/questions")).toBe(true);
    expect(matches("/api/health")).toBe(false);
    expect(matches("/_next/static/app.js")).toBe(false);
    expect(matches("/brand/logo.svg")).toBe(false);
  });
});
