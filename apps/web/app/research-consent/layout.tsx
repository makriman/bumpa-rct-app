import type { Metadata } from "next";
import { publicPageMetadata } from "@/lib/site-metadata";

export const metadata: Metadata = publicPageMetadata({
  path: "/research-consent",
  pageTitle: "Research consent",
  pageDescription:
    "Understand and control how approved Bumpa Bestie conversations may support research.",
});

export default function ResearchConsentLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
