import type { Metadata, MetadataRoute } from "next";

export const siteOrigin = "https://bumpabestie.com";
export const brandAssetVersion = "20260714";

export function brandAssetUrl(path: `/${string}`): string {
  return `${path}?v=${brandAssetVersion}`;
}

export const siteName = "Bumpa Bestie";
export const siteDescription =
  "An independent AI business assistant for connected Bumpa stores, turning sales, product and customer data into clear, practical decisions.";
export const socialImage = {
  url: brandAssetUrl("/brand/social-card.png"),
  width: 1200,
  height: 630,
  alt: "Bumpa Bestie — know your business and move with confidence",
} as const;

export const siteMetadata: Metadata = {
  metadataBase: new URL(siteOrigin),
  applicationName: siteName,
  title: { default: siteName, template: `%s · ${siteName}` },
  description: siteDescription,
  keywords: [
    "Bumpa analytics",
    "Bumpa business assistant",
    "small business analytics",
    "sales insights",
    "inventory insights",
    "commerce analytics",
  ],
  category: "business",
  creator: siteName,
  publisher: siteName,
  referrer: "origin-when-cross-origin",
  formatDetection: {
    telephone: false,
    email: false,
    address: false,
  },
  alternates: { canonical: "/" },
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: brandAssetUrl("/icon.svg"), type: "image/svg+xml" },
      { url: brandAssetUrl("/favicon.ico"), sizes: "32x32" },
    ],
    shortcut: brandAssetUrl("/favicon.ico"),
    apple: [{ url: brandAssetUrl("/apple-icon.png"), sizes: "180x180" }],
  },
  appleWebApp: {
    capable: true,
    title: siteName,
    statusBarStyle: "black-translucent",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  openGraph: {
    type: "website",
    locale: "en_GB",
    url: "/",
    siteName,
    title: siteName,
    description: siteDescription,
    images: [socialImage],
  },
  twitter: {
    card: "summary_large_image",
    title: siteName,
    description: siteDescription,
    images: [socialImage.url],
  },
};

export function publicPageMetadata({
  path,
  pageTitle,
  pageDescription = siteDescription,
}: {
  path: `/${string}` | "/";
  pageTitle?: string;
  pageDescription?: string;
}): Metadata {
  const resolvedTitle = pageTitle ? `${pageTitle} · ${siteName}` : siteName;
  return {
    ...(pageTitle ? { title: pageTitle } : {}),
    description: pageDescription,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      url: path,
      siteName,
      title: resolvedTitle,
      description: pageDescription,
      images: [socialImage],
    },
    twitter: {
      card: "summary_large_image",
      title: resolvedTitle,
      description: pageDescription,
      images: [socialImage.url],
    },
  };
}

export function buildStructuredData() {
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${siteOrigin}/#website`,
        url: `${siteOrigin}/`,
        name: siteName,
        description: siteDescription,
        inLanguage: "en-GB",
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${siteOrigin}/#application`,
        url: `${siteOrigin}/`,
        name: siteName,
        description: siteDescription,
        applicationCategory: "BusinessApplication",
        applicationSubCategory: "Business intelligence",
        operatingSystem: "Web",
        inLanguage: "en-GB",
        image: `${siteOrigin}${socialImage.url}`,
        featureList: [
          "Conversational sales insights",
          "Product and customer analysis",
          "Store data freshness indicators",
          "Role-based team access",
        ],
      },
    ],
  } as const;
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
      allow: ["/", "/research-consent"],
      disallow: [
        "/admin",
        "/api/",
        "/chat",
        "/login",
        "/profile",
        "/research",
        "/settings",
      ],
    },
    sitemap: `${siteOrigin}/sitemap.xml`,
    host: siteOrigin,
  };
}
