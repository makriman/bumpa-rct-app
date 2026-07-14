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

export type AccessSurface = "workspace" | "admin" | "research";

export type PostLoginDestination =
  | { authorized: true; path: string }
  | { authorized: false; surface: AccessSurface };

type SessionAccess = {
  platformRoles: string[];
  memberships: Array<{ role: string; status: string }>;
};

const HOME_BY_SURFACE: Record<AccessSurface, string> = {
  workspace: "/chat",
  admin: "/admin",
  research: "/research",
};

function hostnameSurface(hostname: string): AccessSurface | null {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  if (host.startsWith("admin.")) return "admin";
  if (host.startsWith("research.")) return "research";
  return null;
}

function pathSurface(path: string): AccessSurface | null {
  const pathname = new URL(path, "https://bumpabestie.invalid").pathname;
  if (pathname === "/research-consent") return null;
  if (pathname.startsWith("/admin")) return "admin";
  if (pathname.startsWith("/research")) return "research";
  if (
    ["/chat", "/profile", "/settings"].some((prefix) =>
      pathname.startsWith(prefix),
    )
  ) {
    return "workspace";
  }
  return null;
}

function canAccessSurface(
  surface: AccessSurface,
  { platformRoles, memberships }: SessionAccess,
): boolean {
  if (surface === "admin") {
    return (
      platformRoles.includes("operator") || platformRoles.includes("superadmin")
    );
  }
  if (surface === "research") {
    return (
      platformRoles.includes("researcher") ||
      platformRoles.includes("superadmin")
    );
  }
  return memberships.some(
    (membership) =>
      membership.status === "active" &&
      ["owner", "admin", "member"].includes(membership.role),
  );
}

/** Resolve a post-login route only after proving the session can use it. */
export function resolvePostLoginDestination({
  hostname,
  next,
  platformRoles,
  memberships,
}: {
  hostname: string;
  next: string | null;
  platformRoles: string[];
  memberships: Array<{ role: string; status: string }>;
}): PostLoginDestination {
  const access = { platformRoles, memberships };
  const hostSurface = hostnameSurface(hostname);
  const safeNext = safeNextPath(next);
  const nextSurface = safeNext ? pathSurface(safeNext) : null;

  // A branded hostname is an authorization boundary, not merely a routing
  // preference. Cross-surface `next` values are ignored on those hosts.
  if (hostSurface) {
    if (!canAccessSurface(hostSurface, access)) {
      return { authorized: false, surface: hostSurface };
    }
    return {
      authorized: true,
      path:
        safeNext && nextSurface === hostSurface
          ? safeNext
          : HOME_BY_SURFACE[hostSurface],
    };
  }

  // Middleware supplies protected routes here. A manually supplied public
  // path may still be used, except /login itself, which would create a loop.
  if (
    safeNext &&
    new URL(safeNext, "https://bumpabestie.invalid").pathname !== "/login"
  ) {
    if (nextSurface && !canAccessSurface(nextSurface, access)) {
      return { authorized: false, surface: nextSurface };
    }
    return { authorized: true, path: safeNext };
  }

  for (const surface of ["workspace", "admin", "research"] as const) {
    if (canAccessSurface(surface, access)) {
      return { authorized: true, path: HOME_BY_SURFACE[surface] };
    }
  }

  return { authorized: false, surface: "workspace" };
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
