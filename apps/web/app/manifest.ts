import type { MetadataRoute } from "next";

import { brandAssetUrl, siteDescription, siteName } from "@/lib/site-metadata";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: siteName,
    short_name: "Bestie",
    description: siteDescription,
    start_url: "/login",
    scope: "/",
    display: "standalone",
    background_color: "#f8f5ed",
    theme_color: "#123e31",
    orientation: "any",
    categories: ["business", "productivity"],
    icons: [
      {
        src: brandAssetUrl("/brand/app-icon-192.png"),
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: brandAssetUrl("/brand/app-icon-512.png"),
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: brandAssetUrl("/brand/maskable-icon-512.png"),
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
