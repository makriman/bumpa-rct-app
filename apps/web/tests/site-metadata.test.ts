import { describe, expect, it } from "vitest";

import {
  buildStructuredData,
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
      images: [
        {
          url: "/brand/social-card.png?v=20260714",
          width: 1200,
          height: 630,
        },
      ],
    });
    expect(siteMetadata.twitter).toMatchObject({
      card: "summary_large_image",
      images: ["/brand/social-card.png?v=20260714"],
    });
    expect(siteMetadata).toMatchObject({
      manifest: "/manifest.webmanifest",
      applicationName: "Bumpa Bestie",
      icons: {
        icon: [
          { url: "/icon.svg?v=20260714", type: "image/svg+xml" },
          { url: "/favicon.ico?v=20260714", sizes: "32x32" },
        ],
        shortcut: "/favicon.ico?v=20260714",
        apple: [{ url: "/apple-icon.png?v=20260714", sizes: "180x180" }],
      },
    });
  });

  it("builds route-specific canonical and Open Graph URLs", () => {
    expect(publicPageMetadata({ path: "/" })).toMatchObject({
      alternates: { canonical: "/" },
      openGraph: {
        url: "/",
        title: "Bumpa Bestie",
        images: [
          expect.objectContaining({
            url: "/brand/social-card.png?v=20260714",
          }),
        ],
      },
      twitter: {
        card: "summary_large_image",
        title: "Bumpa Bestie",
      },
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
      twitter: { title: "Privacy notice · Bumpa Bestie" },
    });
  });

  it("publishes truthful WebSite and SoftwareApplication structured data", () => {
    const data = buildStructuredData();
    expect(data["@context"]).toBe("https://schema.org");
    expect(data["@graph"]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          "@type": "WebSite",
          url: "https://bumpabestie.com/",
          name: "Bumpa Bestie",
        }),
        expect.objectContaining({
          "@type": "SoftwareApplication",
          applicationCategory: "BusinessApplication",
          operatingSystem: "Web",
        }),
      ]),
    );
  });

  it("indexes only public product and policy pages", () => {
    expect(buildSitemap().map(({ url }) => url)).toEqual([
      "https://bumpabestie.com",
      "https://bumpabestie.com/about",
      "https://bumpabestie.com/privacy",
      "https://bumpabestie.com/terms",
    ]);
  });

  it("advertises the sitemap while excluding private application surfaces", () => {
    expect(buildRobots()).toEqual({
      rules: {
        userAgent: "*",
        allow: ["/"],
        disallow: ["/api/", "/chat", "/login", "/profile", "/settings"],
      },
      sitemap: "https://bumpabestie.com/sitemap.xml",
      host: "https://bumpabestie.com",
    });
  });
});
