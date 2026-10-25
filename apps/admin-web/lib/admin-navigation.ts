import { safeAppNextPath } from "@bumpabestie/web-foundation";

const ADMIN_DESTINATIONS = [
  "/",
  "/tenants",
  "/administrators",
  "/connections",
  "/sync-runs",
  "/failures",
  "/provider-failures",
  "/usage",
  "/onboarding",
] as const;

/** Validate login return paths against admin routes on this host. */
export function safeAdminNextPath(value: string | null): string | null {
  return safeAppNextPath(value, ADMIN_DESTINATIONS);
}
