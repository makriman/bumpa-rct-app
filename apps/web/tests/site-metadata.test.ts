import { describe, expect, it } from "vitest";

import {
  buildRobots,
  buildSitemap,
  publicPageMetadata,
  siteMetadata,
  siteOrigin,
} from "@/lib/site-metadata";

describe("public site discovery metadata", () => {
  it("publishes one canonical production origin and branded icon", () => {
    expect(siteOrigin).toBe("https://bumpabestie.com");
    expect(siteMetadata.metadataBase?.toString()).toBe(
      "https://bumpabestie.com/",
    );
    expect(siteMetadata.openGraph).toMatchObject({
      type: "website",
      siteName: "Bumpa Bestie",
    });
    expect(siteMetadata.icons).toEqual({
      icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
      shortcut: "/icon.svg",
    });
  });

  it("builds route-specific canonical and Open Graph URLs", () => {
    expect(publicPageMetadata({ path: "/" })).toMatchObject({
      alternates: { canonical: "/" },
      openGraph: { url: "/", title: "Bumpa Bestie" },
    });
    expect(
      publicPageMetadata({
        path: "/privacy",
        pageTitle: "Privacy notice",
      }),
    ).toMatchObject({
      title: "Privacy notice",
      alternates: { canonical: "/privacy" },
      openGraph: {
        url: "/privacy",
        title: "Privacy notice · Bumpa Bestie",
      },
    });
  });

  it("indexes only public product and policy pages", () => {
    expect(buildSitemap().map(({ url }) => url)).toEqual([
      "https://bumpabestie.com",
      "https://bumpabestie.com/about",
      "https://bumpabestie.com/privacy",
      "https://bumpabestie.com/terms",
      "https://bumpabestie.com/research-consent",
    ]);
  });

  it("advertises the sitemap while excluding private application surfaces", () => {
    expect(buildRobots()).toEqual({
      rules: {
        userAgent: "*",
        allow: "/",
        disallow: [
          "/admin/",
          "/api/",
          "/chat",
          "/login",
          "/profile",
          "/research/",
          "/settings/",
        ],
      },
      sitemap: "https://bumpabestie.com/sitemap.xml",
      host: "https://bumpabestie.com",
    });
  });
});
