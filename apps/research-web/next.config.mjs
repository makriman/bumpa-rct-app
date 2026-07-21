import interFontPackage from "@fontsource-variable/inter/package.json" with { type: "json" };
import newsreaderFontPackage from "@fontsource-variable/newsreader/package.json" with { type: "json" };

if (
  interFontPackage.name !== "@fontsource-variable/inter" ||
  newsreaderFontPackage.name !== "@fontsource-variable/newsreader"
) {
  throw new Error(
    "The configured local brand-font packages could not be resolved.",
  );
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    const brandAssetCache = [
      {
        key: "Cache-Control",
        value:
          "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400",
      },
    ];
    return [
      { source: "/brand/:path*", headers: brandAssetCache },
      { source: "/favicon.ico", headers: brandAssetCache },
      { source: "/icon.svg", headers: brandAssetCache },
      { source: "/apple-icon.png", headers: brandAssetCache },
      { source: "/manifest.webmanifest", headers: brandAssetCache },
    ];
  },
};

export default nextConfig;
