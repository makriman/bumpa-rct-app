import type { Metadata } from "next";

export function publicPageMetadata({
  pageTitle,
  pageDescription,
}: {
  path: `/${string}` | "/";
  pageTitle: string;
  pageDescription: string;
}): Metadata {
  return {
    title: pageTitle,
    description: pageDescription,
    robots: { index: false, follow: false, nocache: true },
  };
}
