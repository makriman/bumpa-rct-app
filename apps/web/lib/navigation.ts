import type { AppIconName } from "@/components/app-icon";

export type NavGroup = {
  label: string;
  items: Array<{ label: string; href: string; icon: AppIconName }>;
};

export const userNav: NavGroup[] = [
  {
    label: "Workspace",
    items: [{ label: "Bestie chat", href: "/chat", icon: "sparkles" }],
  },
  {
    label: "Account",
    items: [
      { label: "Profile", href: "/profile", icon: "user" },
      { label: "Team", href: "/settings/team", icon: "users" },
      { label: "WhatsApp", href: "/settings/whatsapp", icon: "chat" },
      { label: "Bumpa connection", href: "/settings/bumpa", icon: "refresh" },
      { label: "Connections", href: "/settings/mcp", icon: "plug" },
    ],
  },
];
