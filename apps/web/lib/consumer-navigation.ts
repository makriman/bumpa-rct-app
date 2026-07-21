const CONSUMER_DESTINATIONS = ["/chat", "/profile", "/settings"] as const;

/** Validate login return paths on the server against consumer-only routes. */
export function safeConsumerNextPath(value: string | null): string | null {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  ) {
    return null;
  }
  try {
    const base = "https://bumpabestie.invalid";
    const target = new URL(value, base);
    if (target.origin !== base) return null;
    if (
      !CONSUMER_DESTINATIONS.some(
        (prefix) =>
          target.pathname === prefix ||
          target.pathname.startsWith(`${prefix}/`),
      )
    ) {
      return null;
    }
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}
