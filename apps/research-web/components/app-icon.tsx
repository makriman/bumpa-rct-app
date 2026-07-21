"use client";

import {
  ArrowSquareOut,
  ArrowsClockwise,
  Bell,
  ChatCircle,
  ChatsCircle,
  CheckCircle,
  DownloadSimple,
  FileText,
  List,
  PlugsConnected,
  Plus,
  Question,
  ShieldCheck,
  SignOut,
  Sparkle,
  SquaresFour,
  Stack,
  Storefront,
  Tag,
  User,
  UsersThree,
  Warning,
  X,
} from "@phosphor-icons/react";

export type AppIconName = keyof typeof icons;

const icons = {
  alert: Warning,
  bell: Bell,
  chat: ChatCircle,
  check: CheckCircle,
  close: X,
  download: DownloadSimple,
  external: ArrowSquareOut,
  file: FileText,
  grid: SquaresFour,
  help: Question,
  layers: Stack,
  logout: SignOut,
  menu: List,
  messages: ChatsCircle,
  plug: PlugsConnected,
  add: Plus,
  refresh: ArrowsClockwise,
  shield: ShieldCheck,
  sparkles: Sparkle,
  store: Storefront,
  tag: Tag,
  user: User,
  users: UsersThree,
} as const;

export function AppIcon({
  name,
  size = 18,
  className,
}: {
  name: AppIconName;
  size?: number;
  className?: string;
}) {
  const Icon = icons[name];
  return (
    <Icon
      className={className}
      size={size}
      weight="regular"
      aria-hidden="true"
      focusable="false"
    />
  );
}
