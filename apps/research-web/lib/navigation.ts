import type { AppIconName } from "@/components/app-icon";

export type NavGroup = {
  label: string;
  items: Array<{ label: string; href: string; icon: AppIconName }>;
};

export const surfaceNav: NavGroup[] = [
  {
    label: "Research",
    items: [
      { label: "Overview", href: "/", icon: "grid" },
      { label: "Questions", href: "/questions", icon: "help" },
      { label: "Conversations", href: "/conversations", icon: "messages" },
      { label: "Classifications", href: "/classifications", icon: "tag" },
      { label: "Cohorts", href: "/cohorts", icon: "users" },
    ],
  },
  {
    label: "Outputs",
    items: [
      { label: "Reports", href: "/reports", icon: "file" },
      { label: "Exports", href: "/exports", icon: "download" },
    ],
  },
];
