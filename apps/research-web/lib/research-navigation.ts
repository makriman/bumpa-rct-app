import { safeAppNextPath } from "@bumpabestie/web-foundation";

const RESEARCH_DESTINATIONS = [
  "/",
  "/questions",
  "/conversations",
  "/classifications",
  "/cohorts",
  "/reports",
  "/exports",
  "/consent",
] as const;

/** Validate login return paths against research routes on this host. */
export function safeResearchNextPath(value: string | null): string | null {
  return safeAppNextPath(value, RESEARCH_DESTINATIONS);
}
