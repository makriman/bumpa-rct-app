import { NextRequest, NextResponse } from "next/server";

import { correlationIdOrNew } from "@/lib/correlation";

const PROTECTED_USER = ["/chat", "/profile", "/settings"];
const PUBLIC_PATHS = new Set([
  "/login",
  "/about",
  "/privacy",
  "/terms",
  "/research-consent",
]);

type SessionView = {
  platform_roles?: string[];
  memberships?: Array<{ role: string; status: string }>;
};

async function authorizeSession(
  request: NextRequest,
  surface: "user" | "admin" | "research",
): Promise<boolean> {
  if (process.env.NEXT_PUBLIC_DEMO_MODE === "true") return true;
  const apiBase = (process.env.API_BASE_URL ?? "http://api:8000").replace(
    /\/$/,
    "",
  );
  try {
    const response = await fetch(`${apiBase}/v1/auth/me`, {
      headers: {
        cookie: request.headers.get("cookie") ?? "",
        "x-correlation-id": correlationIdOrNew(
          request.headers.get("x-correlation-id"),
        ),
      },
      cache: "no-store",
    });
    if (!response.ok) return false;
    const session = (await response.json()) as SessionView;
    const roles = session.platform_roles ?? [];
    if (surface === "admin")
      return roles.includes("operator") || roles.includes("superadmin");
    if (surface === "research")
      return roles.includes("researcher") || roles.includes("superadmin");
    return (session.memberships ?? []).some(
      (membership) =>
        membership.status === "active" &&
        ["owner", "admin", "member"].includes(membership.role),
    );
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest) {
  const host = (request.headers.get("host") ?? "").split(":")[0];
  const path = request.nextUrl.pathname;
  const isLocal = host === "localhost" || host === "127.0.0.1";
  const url = request.nextUrl.clone();
  const isPublic =
    PUBLIC_PATHS.has(path) ||
    path.startsWith("/api/") ||
    path.startsWith("/_next/");

  if (host.startsWith("admin.") && !isPublic) {
    if (
      path.startsWith("/research") ||
      path.startsWith("/chat") ||
      path.startsWith("/settings")
    ) {
      url.pathname = "/admin";
      return NextResponse.redirect(url);
    }
    if (!path.startsWith("/admin") && !path.startsWith("/_next")) {
      const session = request.cookies.get("bb_session")?.value;
      if (!session || !(await authorizeSession(request, "admin"))) {
        url.pathname = "/login";
        url.searchParams.set("next", "/admin");
        return NextResponse.redirect(url);
      }
      url.pathname = path === "/" ? "/admin" : `/admin${path}`;
      return NextResponse.rewrite(url);
    }
  }

  if (host.startsWith("research.") && !isPublic) {
    if (
      path.startsWith("/admin") ||
      path.startsWith("/chat") ||
      path.startsWith("/settings")
    ) {
      url.pathname = "/research";
      return NextResponse.redirect(url);
    }
    if (!path.startsWith("/research") && !path.startsWith("/_next")) {
      const session = request.cookies.get("bb_session")?.value;
      if (!session || !(await authorizeSession(request, "research"))) {
        url.pathname = "/login";
        url.searchParams.set("next", "/research");
        return NextResponse.redirect(url);
      }
      url.pathname = path === "/" ? "/research" : `/research${path}`;
      return NextResponse.rewrite(url);
    }
  }

  const isAdmin = path.startsWith("/admin");
  const isResearch =
    path.startsWith("/research") && path !== "/research-consent";
  const isUser = PROTECTED_USER.some((prefix) => path.startsWith(prefix));
  if (!isLocal && (isAdmin || isResearch || isUser)) {
    const session = request.cookies.get("bb_session")?.value;
    const surface = isAdmin ? "admin" : isResearch ? "research" : "user";
    if (!session || !(await authorizeSession(request, surface))) {
      url.pathname = "/login";
      url.searchParams.set("next", path);
      return NextResponse.redirect(url);
    }
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
