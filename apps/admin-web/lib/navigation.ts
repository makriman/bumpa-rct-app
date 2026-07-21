import type { AppIconName } from "@/components/app-icon";

export type NavGroup = {
  label: string;
  items: Array<{ label: string; href: string; icon: AppIconName }>;
};

export const surfaceNav: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { label: "Overview", href: "/", icon: "grid" },
      { label: "Tenants", href: "/tenants", icon: "store" },
      { label: "Administrators", href: "/administrators", icon: "shield" },
      { label: "Connections", href: "/connections", icon: "plug" },
      { label: "Sync runs", href: "/sync-runs", icon: "refresh" },
    ],
  },
  {
    label: "System",
    items: [
      { label: "Failures", href: "/failures", icon: "alert" },
      {
        label: "Provider failures",
        href: "/provider-failures",
        icon: "layers",
      },
      { label: "Usage", href: "/usage", icon: "grid" },
    ],
  },
];
