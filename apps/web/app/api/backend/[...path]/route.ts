import { NextRequest, NextResponse } from "next/server";

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
  const correlationId =
    request.headers.get("x-correlation-id") ?? crypto.randomUUID();
  headers.set("x-correlation-id", correlationId);
  const hasBody = !["GET", "HEAD"].includes(request.method);
  if (hasBody)
    headers.set(
      "content-type",
      request.headers.get("content-type") ?? "application/json",
    );
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
    const response = new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
        "x-correlation-id": correlationId,
      },
    });
    const setCookie = upstream.headers.get("set-cookie");
    if (setCookie) response.headers.set("set-cookie", setCookie);
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
