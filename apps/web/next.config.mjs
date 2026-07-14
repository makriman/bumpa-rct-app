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
