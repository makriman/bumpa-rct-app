import { NextRequest, NextResponse } from "next/server";
import { hasActiveConsumerMembership } from "@bumpabestie/web-foundation";

import {
  buildContentSecurityPolicy,
  CONTENT_SECURITY_POLICY_HEADER,
  CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER,
  createContentSecurityPolicyNonce,
  CSP_NONCE_REQUEST_HEADER,
} from "@/lib/content-security-policy";
import { correlationIdOrNew } from "@/lib/correlation";

const PROTECTED_PATHS = ["/chat", "/profile", "/settings"];
const PRIVATE_DOCUMENT_ROOTS = ["/chat", "/login", "/profile", "/settings"];

async function hasConsumerAccess(request: NextRequest): Promise<boolean> {
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
    return hasActiveConsumerMembership(session);
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest) {
  const nonce = createContentSecurityPolicyNonce();
  const policy = buildContentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.delete(CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER);
  requestHeaders.set(CONTENT_SECURITY_POLICY_HEADER, policy);
  requestHeaders.set(CSP_NONCE_REQUEST_HEADER, nonce);

  const path = request.nextUrl.pathname;
  const preventIndexing = PRIVATE_DOCUMENT_ROOTS.some(
    (root) => path === root || path.startsWith(`${root}/`),
  );
  const secure = (response: NextResponse) => {
    response.headers.set(CONTENT_SECURITY_POLICY_HEADER, policy);
    response.headers.set("Cache-Control", "private, no-store");
    if (preventIndexing) {
      response.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
    }
    response.headers.delete(CSP_NONCE_REQUEST_HEADER);
    return response;
  };

  const protectedPath = PROTECTED_PATHS.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
  if (protectedPath) {
    const session = request.cookies.get("bb_session")?.value;
    if (!session || !(await hasConsumerAccess(request))) {
      const url = request.nextUrl.clone();
      url.pathname = "/login";
      url.search = "";
      url.searchParams.set("next", `${path}${request.nextUrl.search}`);
      return secure(NextResponse.redirect(url));
    }
  }

  return secure(NextResponse.next({ request: { headers: requestHeaders } }));
}

export const config = {
  matcher: [
    "/((?!api|_next/static|_next/image|brand/|brand-mark.svg|favicon.ico|icon.svg|apple-icon.png|manifest.webmanifest|robots.txt|sitemap.xml).*)",
  ],
};
