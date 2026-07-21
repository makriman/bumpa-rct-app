import type { Metadata } from "next";
import { publicPageMetadata } from "@/lib/site-metadata";

export const metadata: Metadata = publicPageMetadata({
  path: "/consent",
  pageTitle: "Research consent",
  pageDescription:
    "Understand and control how approved Bumpa Bestie conversations may support research.",
});

export default function ConsentLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
