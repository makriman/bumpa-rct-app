import type { Metadata, MetadataRoute } from "next";

export const siteOrigin = "https://bumpabestie.com";

const title = "Bumpa Bestie";
const description = "Your AI business partner, powered by your Bumpa data.";

export const siteMetadata: Metadata = {
  metadataBase: new URL(siteOrigin),
  title: { default: title, template: `%s · ${title}` },
  description,
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    shortcut: "/icon.svg",
  },
  openGraph: {
    type: "website",
    siteName: title,
    description,
  },
};

export function publicPageMetadata({
  path,
  pageTitle,
  pageDescription = description,
}: {
  path: `/${string}` | "/";
  pageTitle?: string;
  pageDescription?: string;
}): Metadata {
  const resolvedTitle = pageTitle ? `${pageTitle} · ${title}` : title;
  return {
    ...(pageTitle ? { title: pageTitle } : {}),
    description: pageDescription,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      url: path,
      siteName: title,
      title: resolvedTitle,
      description: pageDescription,
    },
  };
}

const publicSitemapRoutes = [
  "",
  "/about",
  "/privacy",
  "/terms",
  "/research-consent",
];

export function buildSitemap(): MetadataRoute.Sitemap {
  return publicSitemapRoutes.map((route, index) => ({
    url: `${siteOrigin}${route}`,
    changeFrequency: index === 0 ? "weekly" : "monthly",
    priority: index === 0 ? 1 : 0.6,
  }));
}

export function buildRobots(): MetadataRoute.Robots {
  return {
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
    sitemap: `${siteOrigin}/sitemap.xml`,
    host: siteOrigin,
  };
}
