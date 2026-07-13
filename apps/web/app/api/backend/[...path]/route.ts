import { NextRequest, NextResponse } from "next/server";
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
  "x-tenant-id",
  "x-access-reason",
] as const;

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
  // Caddy is the only public listener and normalises these headers before the
  // request reaches Next. Preserve them across the internal BFF hop so API-side
  // abuse controls rate-limit the actual edge client, not the shared web pod.
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);
  const realIp = request.headers.get("x-real-ip");
  if (realIp) headers.set("x-real-ip", realIp);
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
    const response = new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
        "x-correlation-id": responseCorrelationId,
      },
    });
    const setCookie = upstream.headers.get("set-cookie");
    if (setCookie) response.headers.set("set-cookie", setCookie);
    for (const name of [
      "content-disposition",
      "retry-after",
      "www-authenticate",
    ]) {
      const value = upstream.headers.get(name);
      if (value) response.headers.set(name, value);
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
export const PATCH = proxy;
export const DELETE = proxy;
