import { isRecord } from "./api-error";

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function platformRolesFromSession(value: unknown): string[] {
  return isRecord(value) ? strings(value.platform_roles) : [];
}

export function hasActiveConsumerMembership(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.memberships)) return false;
  return value.memberships.some(
    (membership) =>
      isRecord(membership) &&
      membership.status === "active" &&
      typeof membership.role === "string" &&
      ["owner", "admin", "member"].includes(membership.role),
  );
}
