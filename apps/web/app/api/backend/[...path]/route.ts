import { NextRequest, NextResponse } from "next/server";
import { isIP } from "node:net";
import {
  correlationIdOrNew,
  isCanonicalCorrelationId,
} from "@/lib/correlation";

const configuredOrigin =
  process.env.INTERNAL_API_BASE_URL ??
  process.env.API_BASE_URL ??
  "http://127.0.0.1:8000";
const API_ORIGIN = configuredOrigin.endsWith("/v1")
  ? configuredOrigin
  : `${configuredOrigin.replace(/\/$/, "")}/v1`;
const ALLOWED_ROOTS = new Set([
  "auth",
  "chat",
  "settings",
  "tenants",
  "admin",
  "research",
  "bumpa",
  "mcp",
]);
const MAX_REQUEST_BYTES = 1024 * 1024;
// These application-level headers are required by specific API contracts. Keep
// this list deliberately narrow: arbitrary browser headers (including
// Authorization and hop-by-hop transport headers) must never cross the BFF.
const FORWARDED_APPLICATION_HEADERS = [
  "idempotency-key",
  "if-match",
  "x-tenant-id",
  "x-access-reason",
] as const;

function caddyClientIp(request: NextRequest): string | null {
  const value = request.headers.get("x-bumpa-client-ip");
  if (
    !value ||
    value !== value.trim() ||
    value.includes(",") ||
    isIP(value) === 0
  ) {
    return null;
  }
  return value;
}

async function proxy(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  if (!path.length || !ALLOWED_ROOTS.has(path[0])) {
    return NextResponse.json(
      { detail: "Unsupported API route" },
      { status: 404 },
    );
  }
  const target = new URL(
    `${API_ORIGIN.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`,
  );
  request.nextUrl.searchParams.forEach((value, key) =>
    target.searchParams.append(key, value),
  );
  const headers = new Headers({ Accept: "application/json" });
  const cookie = request.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  // Browser-visible forwarding headers are untrusted. Caddy overwrites this
  // private single-IP header at the sole public listener after validating the
  // Cloudflare peer; direct development traffic safely omits it.
  const clientIp = caddyClientIp(request);
  if (clientIp) headers.set("x-forwarded-for", clientIp);
  // Preserve the browser's source metadata. Production cookie-authenticated
  // mutations are validated by the API, so the private BFF hop must not erase
  // an attacker-controlled Origin and accidentally turn it into trusted traffic.
  const origin = request.headers.get("origin");
  if (origin) headers.set("origin", origin);
  const referer = request.headers.get("referer");
  if (referer) headers.set("referer", referer);
  for (const name of FORWARDED_APPLICATION_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const correlationId = correlationIdOrNew(
    request.headers.get("x-correlation-id"),
  );
  headers.set("x-correlation-id", correlationId);
  const hasBody = !["GET", "HEAD"].includes(request.method);
  if (hasBody)
    headers.set(
      "content-type",
      request.headers.get("content-type") ?? "application/json",
    );
  try {
    const body = hasBody ? await request.arrayBuffer() : undefined;
    if (body && body.byteLength > MAX_REQUEST_BYTES) {
      return NextResponse.json(
        { detail: "Request body is too large" },
        { status: 413, headers: { "x-correlation-id": correlationId } },
      );
    }
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
    });
    const upstreamCorrelationId = upstream.headers.get("x-correlation-id");
    const responseCorrelationId = isCanonicalCorrelationId(
      upstreamCorrelationId,
    )
      ? upstreamCorrelationId
      : correlationId;
    const upstreamBody = await upstream.arrayBuffer();
    let responseBody: ArrayBuffer | string = upstreamBody;
    let responseStatus = upstream.status;
    let forwardSetCookie = true;
    if (
      upstream.ok &&
      path.length === 2 &&
      path[0] === "auth" &&
      path[1] === "verify-otp"
    ) {
      try {
        const payload = JSON.parse(
          new TextDecoder().decode(upstreamBody),
        ) as Record<string, unknown> | null;
        if (payload && typeof payload === "object") {
          delete payload.access_token;
          responseBody = JSON.stringify(payload);
        }
      } catch {
        responseBody = JSON.stringify({
          detail: "Authentication response was invalid",
        });
        responseStatus = 502;
        forwardSetCookie = false;
      }
    }
    const response = new NextResponse(responseBody, {
      status: responseStatus,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
        "x-correlation-id": responseCorrelationId,
      },
    });
    const setCookie = upstream.headers.get("set-cookie");
    if (setCookie && forwardSetCookie)
      response.headers.set("set-cookie", setCookie);
    for (const name of [
      "content-disposition",
      "retry-after",
      "www-authenticate",
    ]) {
      const value = upstream.headers.get(name);
      if (value) response.headers.set(name, value);
    }
    const redirectLocation = upstream.headers.get("location");
    if (redirectLocation && upstream.status >= 300 && upstream.status < 400) {
      const redirect = new URL(redirectLocation, request.nextUrl.origin);
      if (redirect.origin !== request.nextUrl.origin) {
        return NextResponse.json(
          { detail: "The API returned an invalid redirect target" },
          {
            status: 502,
            headers: { "x-correlation-id": responseCorrelationId },
          },
        );
      }
      response.headers.set("location", redirect.toString());
    }
    return response;
  } catch {
    return NextResponse.json(
      {
        detail:
          "The local API is unavailable. Start FastAPI or enable the labelled demo fallback.",
      },
      { status: 503, headers: { "x-correlation-id": correlationId } },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
