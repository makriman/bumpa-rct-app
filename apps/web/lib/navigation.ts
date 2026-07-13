import type { Role } from "./demo-data";
import type { AppIconName } from "@/components/app-icon";

export type NavItem = { label: string; href: string; icon: AppIconName };
export type NavGroup = { label: string; items: NavItem[] };

export const userNav: NavGroup[] = [
  {
    label: "Workspace",
    items: [{ label: "Bestie chat", href: "/chat", icon: "sparkles" }],
  },
  {
    label: "Your business",
    items: [
      { label: "Profile", href: "/profile", icon: "user" },
      { label: "Team", href: "/settings/team", icon: "users" },
      { label: "WhatsApp", href: "/settings/whatsapp", icon: "chat" },
      { label: "Bumpa connection", href: "/settings/bumpa", icon: "refresh" },
      { label: "Connections", href: "/settings/mcp", icon: "plug" },
    ],
  },
];

export const adminNav: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { label: "Overview", href: "/admin", icon: "grid" },
      { label: "Tenants", href: "/admin/tenants", icon: "store" },
      { label: "Administrators", href: "/admin/users", icon: "shield" },
      { label: "Connections", href: "/admin/connections", icon: "plug" },
      { label: "Sync runs", href: "/admin/sync", icon: "refresh" },
    ],
  },
  {
    label: "System",
    items: [
      { label: "Failures", href: "/admin/errors", icon: "alert" },
      { label: "Provider failures", href: "/admin/providers", icon: "layers" },
      { label: "Usage", href: "/admin/usage", icon: "grid" },
    ],
  },
];

export const researchNav: NavGroup[] = [
  {
    label: "Research",
    items: [
      { label: "Overview", href: "/research", icon: "grid" },
      { label: "Questions", href: "/research/questions", icon: "help" },
      {
        label: "Conversations",
        href: "/research/conversations",
        icon: "messages",
      },
      {
        label: "Classifications",
        href: "/research/classifications",
        icon: "tag",
      },
      { label: "Cohorts", href: "/research/cohorts", icon: "users" },
    ],
  },
  {
    label: "Outputs",
    items: [
      { label: "Reports", href: "/research/reports", icon: "file" },
      { label: "Exports", href: "/research/exports", icon: "download" },
    ],
  },
];

export function homeForRole(role: Role): string {
  if (role === "operator") return "/admin";
  if (role === "researcher") return "/research";
  if (role === "superadmin") return "/admin";
  return "/chat";
}

/** Accept only local application paths for post-authentication navigation. */
export function safeNextPath(value: string | null): string | null {
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
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}
