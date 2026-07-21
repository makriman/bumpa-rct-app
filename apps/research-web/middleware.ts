import { NextRequest, NextResponse } from "next/server";
import { platformRolesFromSession } from "@bumpabestie/web-foundation";
import {
  buildContentSecurityPolicy,
  CONTENT_SECURITY_POLICY_HEADER,
  CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER,
  createContentSecurityPolicyNonce,
  CSP_NONCE_REQUEST_HEADER,
} from "@/lib/content-security-policy";
import { correlationIdOrNew } from "@/lib/correlation";

async function isAuthorised(request: NextRequest): Promise<boolean> {
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
    const session: unknown = await response.json();
    return platformRolesFromSession(session).some((role) =>
      ["researcher", "superadmin"].includes(role),
    );
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname;
  const nonce = createContentSecurityPolicyNonce();
  const policy = buildContentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.delete(CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER);
  requestHeaders.set(CONTENT_SECURITY_POLICY_HEADER, policy);
  requestHeaders.set(CSP_NONCE_REQUEST_HEADER, nonce);
  const secure = (response: NextResponse) => {
    response.headers.set(CONTENT_SECURITY_POLICY_HEADER, policy);
    response.headers.set("Cache-Control", "private, no-store");
    response.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
    response.headers.delete(CSP_NONCE_REQUEST_HEADER);
    return response;
  };
  if (path === "/research" || path.startsWith("/research/")) {
    const url = request.nextUrl.clone();
    url.pathname = path === "/research" ? "/" : path.slice("/research".length);
    return secure(NextResponse.redirect(url, 308));
  }
  if (path === "/research-consent") {
    const url = request.nextUrl.clone();
    url.pathname = "/consent";
    return secure(NextResponse.redirect(url, 308));
  }
  const publicConsent = path === "/consent";
  if (
    !publicConsent &&
    path !== "/login" &&
    !request.cookies.get("bb_session")?.value
  ) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("next", `${path}${request.nextUrl.search}`);
    return secure(NextResponse.redirect(url));
  }
  if (!publicConsent && path !== "/login" && !(await isAuthorised(request))) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("next", `${path}${request.nextUrl.search}`);
    return secure(NextResponse.redirect(url));
  }
  return secure(NextResponse.next({ request: { headers: requestHeaders } }));
}

export const config = {
  matcher: [
    "/((?!api|_next/static|_next/image|brand/|favicon.ico|icon.svg|apple-icon.png|robots.txt).*)",
  ],
};
