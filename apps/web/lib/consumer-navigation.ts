import { safeAppNextPath } from "@bumpabestie/web-foundation";

const CONSUMER_DESTINATIONS = ["/chat", "/profile", "/settings"] as const;

/** Validate login return paths on the server against consumer-only routes. */
export function safeConsumerNextPath(value: string | null): string | null {
  return safeAppNextPath(value, CONSUMER_DESTINATIONS, { includeHash: true });
}
