import type { Role } from "./demo-data";

export type NavItem = { label: string; href: string; icon: string };
export type NavGroup = { label: string; items: NavItem[] };

export const userNav: NavGroup[] = [
  {
    label: "Workspace",
    items: [{ label: "Bestie chat", href: "/chat", icon: "✦" }],
  },
  {
    label: "Your business",
    items: [
      { label: "Profile", href: "/profile", icon: "◉" },
      { label: "Team", href: "/settings/team", icon: "♢" },
      { label: "WhatsApp", href: "/settings/whatsapp", icon: "◌" },
      { label: "Bumpa connection", href: "/settings/bumpa", icon: "↻" },
      { label: "Connections", href: "/settings/mcp", icon: "⌘" },
    ],
  },
];

export const adminNav: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { label: "Overview", href: "/admin", icon: "▦" },
      { label: "Tenants", href: "/admin/tenants", icon: "⌂" },
      { label: "Users", href: "/admin/users", icon: "♢" },
      { label: "Sync runs", href: "/admin/sync", icon: "↻" },
    ],
  },
  {
    label: "System",
    items: [
      { label: "Failures", href: "/admin/errors", icon: "△" },
      { label: "Usage", href: "/admin/usage", icon: "◫" },
    ],
  },
];

export const researchNav: NavGroup[] = [
  {
    label: "Research",
    items: [
      { label: "Overview", href: "/research", icon: "▦" },
      { label: "Questions", href: "/research/questions", icon: "?" },
      { label: "Conversations", href: "/research/conversations", icon: "◌" },
      {
        label: "Classifications",
        href: "/research/classifications",
        icon: "⌗",
      },
      { label: "Cohorts", href: "/research/cohorts", icon: "♢" },
    ],
  },
  {
    label: "Outputs",
    items: [
      { label: "Reports", href: "/research/reports", icon: "▤" },
      { label: "Exports", href: "/research/exports", icon: "⇩" },
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
