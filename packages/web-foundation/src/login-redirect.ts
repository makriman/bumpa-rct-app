const REDIRECT_BASE = "https://app.invalid";

export type SafeNextPathOptions = {
  includeHash?: boolean;
};

/**
 * Keep post-login navigation on an allowlisted same-origin path.
 * The root path is an exact match. A prefix check on "/" would treat
 * "//evil.example" as allowed after "/.//evil.example" is normalized.
 */
export function safeAppNextPath(
  value: string | null,
  allowedPaths: readonly string[],
  options?: SafeNextPathOptions,
): string | null {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    /[\u0000-\u001F\u007F]/.test(value)
  ) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(value, REDIRECT_BASE);
  } catch {
    return null;
  }
  if (url.origin !== REDIRECT_BASE) return null;
  const { pathname } = url;
  if (
    !pathname.startsWith("/") ||
    pathname.startsWith("//") ||
    pathname.includes("\\")
  ) {
    return null;
  }
  const allowed = allowedPaths.some((path) => {
    if (!path.startsWith("/") || path.startsWith("//")) return false;
    if (path === "/") return pathname === "/";
    return pathname === path || pathname.startsWith(`${path}/`);
  });
  if (!allowed) return null;
  const hash = options?.includeHash ? url.hash : "";
  const destination = `${pathname}${url.search}${hash}`;
  if (
    !destination.startsWith("/") ||
    destination.startsWith("//") ||
    destination.includes("\\")
  ) {
    return null;
  }
  return destination;
}
