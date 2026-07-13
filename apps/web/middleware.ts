import { NextRequest, NextResponse } from "next/server";

import {
  buildContentSecurityPolicy,
  CONTENT_SECURITY_POLICY_HEADER,
  CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER,
  createContentSecurityPolicyNonce,
  CSP_NONCE_REQUEST_HEADER,
} from "@/lib/content-security-policy";
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

type DocumentSecurity = {
  contentSecurityPolicy: string;
  requestHeaders: Headers;
};

function documentSecurity(request: NextRequest): DocumentSecurity {
  const nonce = createContentSecurityPolicyNonce();
  const contentSecurityPolicy = buildContentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);

  // Never let untrusted edge input choose the nonce or a policy that Next's
  // renderer may parse. Caddy strips these too; overwriting here keeps direct
  // development and test traffic equally safe.
  requestHeaders.delete(CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER);
  requestHeaders.set(CONTENT_SECURITY_POLICY_HEADER, contentSecurityPolicy);
  requestHeaders.set(CSP_NONCE_REQUEST_HEADER, nonce);
  return { contentSecurityPolicy, requestHeaders };
}

function secureDocumentResponse(
  response: NextResponse,
  contentSecurityPolicy: string,
): NextResponse {
  response.headers.set(CONTENT_SECURITY_POLICY_HEADER, contentSecurityPolicy);
  // A nonce belongs to exactly one rendered response and must never be shared
  // by an intermediary cache.
  response.headers.set("Cache-Control", "private, no-store");
  response.headers.delete(CSP_NONCE_REQUEST_HEADER);
  return response;
}

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
  const { contentSecurityPolicy, requestHeaders } = documentSecurity(request);
  const redirect = (url: URL) =>
    secureDocumentResponse(NextResponse.redirect(url), contentSecurityPolicy);
  const rewrite = (url: URL) =>
    secureDocumentResponse(
      NextResponse.rewrite(url, { request: { headers: requestHeaders } }),
      contentSecurityPolicy,
    );
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
      return redirect(url);
    }
    if (!path.startsWith("/admin") && !path.startsWith("/_next")) {
      const session = request.cookies.get("bb_session")?.value;
      if (!session || !(await authorizeSession(request, "admin"))) {
        url.pathname = "/login";
        url.searchParams.set("next", "/admin");
        return redirect(url);
      }
      url.pathname = path === "/" ? "/admin" : `/admin${path}`;
      return rewrite(url);
    }
  }

  if (host.startsWith("research.") && !isPublic) {
    if (
      path.startsWith("/admin") ||
      path.startsWith("/chat") ||
      path.startsWith("/settings")
    ) {
      url.pathname = "/research";
      return redirect(url);
    }
    if (!path.startsWith("/research") && !path.startsWith("/_next")) {
      const session = request.cookies.get("bb_session")?.value;
      if (!session || !(await authorizeSession(request, "research"))) {
        url.pathname = "/login";
        url.searchParams.set("next", "/research");
        return redirect(url);
      }
      url.pathname = path === "/" ? "/research" : `/research${path}`;
      return rewrite(url);
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
      return redirect(url);
    }
  }
  return secureDocumentResponse(
    NextResponse.next({ request: { headers: requestHeaders } }),
    contentSecurityPolicy,
  );
}

export const config = {
  // Keep RSC and segment-prefetch variants inside the matcher because this
  // boundary also performs authorization. Only non-document resources bypass
  // it; do not add the common prefetch-header exclusion here.
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|icon.svg|robots.txt|sitemap.xml).*)",
  ],
};
