export const CONTENT_SECURITY_POLICY_HEADER = "Content-Security-Policy";
export const CONTENT_SECURITY_POLICY_REPORT_ONLY_HEADER =
  "Content-Security-Policy-Report-Only";
export const CSP_NONCE_REQUEST_HEADER = "x-nonce";

/**
 * Generate a request-scoped CSP nonce from Web Crypto. A UUID contains 122
 * random bits; removing separators leaves a token accepted by the CSP
 * base64-value grammar without depending on a Node-only encoder.
 */
export function createContentSecurityPolicyNonce(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

/**
 * Keep executable code nonce-gated. The application still has deliberate JSX
 * style attributes, including data-driven chart geometry, so inline CSS is
 * isolated to style-src-attr until those attributes are refactored. It must
 * never be widened into script-src or style-src.
 */
export function buildContentSecurityPolicy(
  nonce: string,
  isDevelopment = process.env.NODE_ENV === "development",
): string {
  const directives = [
    "default-src 'none'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${
      isDevelopment ? " 'unsafe-eval'" : ""
    }`,
    "script-src-attr 'none'",
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src 'self'${isDevelopment ? " ws: wss:" : ""}`,
    "manifest-src 'self'",
    "media-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
  ];
  return `${directives.join("; ")};`;
}
