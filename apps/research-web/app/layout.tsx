import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { connection } from "next/server";
import "flag-icons/css/flag-icons.min.css";
import "./globals.css";

const inter = localFont({
  src: "../node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
  variable: "--font-brand-sans",
  display: "swap",
  weight: "100 900",
  style: "normal",
  adjustFontFallback: "Arial",
});

const newsreader = localFont({
  src: "../node_modules/@fontsource-variable/newsreader/files/newsreader-latin-wght-normal.woff2",
  variable: "--font-brand-display",
  display: "swap",
  weight: "200 800",
  style: "normal",
  adjustFontFallback: "Times New Roman",
});

export const metadata: Metadata = {
  title: {
    default: "Bumpa Bestie Research",
    template: "%s · Bumpa Bestie Research",
  },
  description: "Restricted, consent-aware research tools for Bumpa Bestie.",
  robots: { index: false, follow: false, nocache: true },
};

export const viewport: Viewport = {
  colorScheme: "light",
  themeColor: "#123e31",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await connection();
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body className={`${inter.variable} ${newsreader.variable}`}>
        <a className="skip-link" href="#main-content">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
