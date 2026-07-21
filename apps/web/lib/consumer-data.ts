export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export function statusTone(status: string): Tone {
  const normalized = status.toLowerCase();
  if (
    [
      "active",
      "approved",
      "healthy",
      "success",
      "ready",
      "good",
      "connected",
    ].includes(normalized)
  ) {
    return "success";
  }
  if (["failed", "offline", "suspended", "high", "open"].includes(normalized)) {
    return "danger";
  }
  if (
    [
      "pending",
      "partial",
      "attention",
      "onboarding",
      "medium",
      "running",
      "setup",
    ].includes(normalized)
  ) {
    return "warning";
  }
  if (["web", "whatsapp"].includes(normalized)) return "info";
  return "neutral";
}

export function workspaceRoleLabel(role: string | null | undefined): string {
  if (!role) return "Member";
  if (role.toLowerCase() === "admin") return "Manager";
  return `${role[0].toUpperCase()}${role.slice(1)}`;
}
