import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST, PUT } from "@/app/api/backend/[...path]/route";

afterEach(() => {
  vi.restoreAllMocks();
});

function context(...path: string[]) {
  return { params: Promise.resolve({ path }) };
}

describe("same-origin backend proxy", () => {
  it("keeps the OAuth callback and final redirect on the cookie-owning origin", async () => {
    const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 303,
        headers: {
          location: "https://bumpabestie.example/settings/mcp?oauth=success",
        },
      }),
    );
    const request = new NextRequest(
      "https://bumpabestie.example/api/backend/settings/mcp-oauth/callback?state=encrypted&code=one-time",
      { headers: { cookie: "bb_session=signed" } },
    );

    const response = await GET(
      request,
      context("settings", "mcp-oauth", "callback"),
    );

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://bumpabestie.example/settings/mcp?oauth=success",
    );
    const [url, init] = upstream.mock.calls[0];
    expect(String(url)).toBe(
      "http://127.0.0.1:8000/v1/settings/mcp-oauth/callback?state=encrypted&code=one-time",
    );
    expect(new Headers(init?.headers).get("cookie")).toBe("bb_session=signed");
  });

  it("rejects a cross-origin redirect returned by the API", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 303,
        headers: { location: "https://attacker.example/steal" },
      }),
    );
    const request = new NextRequest(
      "https://bumpabestie.example/api/backend/settings/mcp-oauth/callback?state=encrypted&code=one-time",
      { headers: { cookie: "bb_session=signed" } },
    );

    const response = await GET(
      request,
      context("settings", "mcp-oauth", "callback"),
    );

    expect(response.status).toBe(502);
    expect(response.headers.get("location")).toBeNull();
  });

  it("preserves the security and observability headers required by the API", async () => {
    const correlationId = "550e8400-e29b-41d4-a716-446655440000";
    const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "queued" }), {
        status: 202,
        headers: {
          "content-type": "application/json",
          "retry-after": "3",
          "x-correlation-id": correlationId,
        },
      }),
    );
    const request = new NextRequest(
      "https://bumpabestie.example/api/backend/bumpa/sync/latest?window=30",
      {
        method: "POST",
        body: JSON.stringify({ requested: true }),
        headers: {
          cookie: "bb_session=signed",
          origin: "https://bumpabestie.example",
          referer: "https://bumpabestie.example/settings/bumpa",
          "x-correlation-id": correlationId,
          "x-bumpa-client-ip": "203.0.113.9",
          "x-forwarded-for": "198.51.100.91, 192.0.2.44",
          "x-real-ip": "198.51.100.92",
          "idempotency-key": "sync-019f",
          "if-match": "7",
          "x-tenant-id": "tenant-live",
          "x-access-reason": "Investigate approved study anomaly",
          authorization: "Bearer browser-controlled-token",
          connection: "keep-alive, x-internal-secret",
          "x-internal-secret": "must-not-cross-bff",
        },
      },
    );

    const response = await POST(request, context("bumpa", "sync", "latest"));

    expect(response.status).toBe(202);
    expect(response.headers.get("x-correlation-id")).toBe(correlationId);
    expect(response.headers.get("retry-after")).toBe("3");
    const [url, init] = upstream.mock.calls[0];
    expect(String(url)).toBe(
      "http://127.0.0.1:8000/v1/bumpa/sync/latest?window=30",
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("bb_session=signed");
    expect(headers.get("origin")).toBe("https://bumpabestie.example");
    expect(headers.get("referer")).toBe(
      "https://bumpabestie.example/settings/bumpa",
    );
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.9");
    expect(headers.has("x-real-ip")).toBe(false);
    expect(headers.get("x-correlation-id")).toBe(correlationId);
    expect(headers.get("idempotency-key")).toBe("sync-019f");
    expect(headers.get("if-match")).toBe("7");
    expect(headers.get("x-tenant-id")).toBe("tenant-live");
    expect(headers.get("x-access-reason")).toBe(
      "Investigate approved study anomaly",
    );
    expect(headers.has("authorization")).toBe(false);
    expect(headers.has("connection")).toBe(false);
    expect(headers.has("x-internal-secret")).toBe(false);
  });

  it("forwards audited platform access grants as PUT requests", async () => {
    const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          user_id: "target-user",
          platform_roles: ["operator"],
          has_active_mapping: true,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    const request = new NextRequest(
      "https://admin.bumpabestie.example/api/backend/admin/platform-access/target-user/operator",
      {
        method: "PUT",
        headers: {
          cookie: "bb_session=signed",
          origin: "https://admin.bumpabestie.example",
          "x-access-reason": "Approved pilot collaborator access",
        },
      },
    );

    const response = await PUT(
      request,
      context("admin", "platform-access", "target-user", "operator"),
    );

    expect(response.status).toBe(200);
    const [url, init] = upstream.mock.calls[0];
    expect(String(url)).toBe(
      "http://127.0.0.1:8000/v1/admin/platform-access/target-user/operator",
    );
    expect(init?.method).toBe("PUT");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("bb_session=signed");
    expect(headers.get("origin")).toBe("https://admin.bumpabestie.example");
    expect(headers.get("x-access-reason")).toBe(
      "Approved pilot collaborator access",
    );
  });

  it.each([
    ["a forwarding chain", "203.0.113.9, 198.51.100.4"],
    ["a non-canonical IPv4 value", "010.0.0.1"],
    ["a non-IP value", "client-controlled"],
  ])(
    "drops %s from the Caddy-owned client-IP boundary",
    async (_label, value) => {
      const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
      const request = new NextRequest(
        "https://bumpabestie.example/api/backend/auth/me",
        {
          headers: {
            "x-bumpa-client-ip": value,
            "x-forwarded-for": "192.0.2.77",
            "x-real-ip": "192.0.2.78",
          },
        },
      );

      const response = await GET(request, context("auth", "me"));

      expect(response.status).toBe(200);
      const headers = new Headers(upstream.mock.calls[0][1]?.headers);
      expect(headers.has("x-forwarded-for")).toBe(false);
      expect(headers.has("x-real-ip")).toBe(false);
      expect(headers.has("x-bumpa-client-ip")).toBe(false);
    },
  );

  it("forwards one canonical IPv6 client address", async () => {
    const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const request = new NextRequest(
      "https://bumpabestie.example/api/backend/auth/me",
      { headers: { "x-bumpa-client-ip": "2001:db8::9" } },
    );

    await GET(request, context("auth", "me"));

    const headers = new Headers(upstream.mock.calls[0][1]?.headers);
    expect(headers.get("x-forwarded-for")).toBe("2001:db8::9");
  });

  it("replaces secret-shaped IDs and returns the API's validated correlation ID", async () => {
    const phoneCanary = "+2348000000000";
    const otpCanary = "123456";
    const untrustedCorrelation = [
      phoneCanary,
      otpCanary,
      "bearer",
      "token",
    ].join("-");
    const returnedCorrelation = "8f14e45f-ea8b-4c6d-a321-9d4e5f6a7b8c";
    const upstream = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Rejected" }), {
        status: 403,
        headers: {
          "content-type": "application/json",
          "x-correlation-id": returnedCorrelation,
        },
      }),
    );
    const request = new NextRequest(
      "https://bumpabestie.example/api/backend/auth/me",
      {
        method: "POST",
        headers: { "x-correlation-id": untrustedCorrelation },
      },
    );

    const response = await POST(request, context("auth", "me"));

    const forwarded = new Headers(upstream.mock.calls[0][1]?.headers).get(
      "x-correlation-id",
    );
    expect(forwarded).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(forwarded).not.toBe(untrustedCorrelation);
    expect(response.headers.get("x-correlation-id")).toBe(returnedCorrelation);
    expect(response.headers.get("x-correlation-id")).not.toBe(forwarded);
  });

  it("keeps the OTP bearer token inside the HttpOnly cookie boundary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "browser-readable-token-must-not-cross-bff",
          token_type: "bearer",
          user: { id: "user-1", name: "Owner" },
        }),
        {
          status: 200,
          headers: {
            "content-type": "application/json",
            "set-cookie": "bb_session=signed; HttpOnly; Secure; SameSite=Lax",
          },
        },
      ),
    );

    const response = await POST(
      new NextRequest(
        "https://bumpabestie.example/api/backend/auth/verify-otp",
        {
          method: "POST",
          body: JSON.stringify({
            phone_e164: "+2348012345678",
            code: "246810",
          }),
        },
      ),
      context("auth", "verify-otp"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    await expect(response.json()).resolves.toEqual({
      token_type: "bearer",
      user: { id: "user-1", name: "Owner" },
    });
  });

  it("rejects oversized bodies before contacting the API", async () => {
    const upstream = vi.spyOn(globalThis, "fetch");
    const response = await POST(
      new NextRequest("https://bumpabestie.example/api/backend/chat/messages", {
        method: "POST",
        body: "x".repeat(1024 * 1024 + 1),
      }),
      context("chat", "messages"),
    );

    expect(response.status).toBe(413);
    await expect(response.json()).resolves.toEqual({
      detail: "Request body is too large",
    });
    expect(upstream).not.toHaveBeenCalled();
  });

  it("does not proxy routes outside the explicit API allowlist", async () => {
    const upstream = vi.spyOn(globalThis, "fetch");
    const response = await POST(
      new NextRequest(
        "https://bumpabestie.example/api/backend/internal/secrets",
        {
          method: "POST",
        },
      ),
      context("internal", "secrets"),
    );

    expect(response.status).toBe(404);
    expect(upstream).not.toHaveBeenCalled();
  });
});
