import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";

function pngDimensions(assetPath: string): [number, number] {
  const bytes = readFileSync(path.resolve(process.cwd(), assetPath));
  expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

describe("generated brand assets", () => {
  it("ships social, install and Apple artwork at the advertised dimensions", () => {
    expect(pngDimensions("public/brand/social-card.png")).toEqual([1200, 630]);
    expect(pngDimensions("public/brand/app-icon-192.png")).toEqual([192, 192]);
    expect(pngDimensions("public/brand/app-icon-512.png")).toEqual([512, 512]);
    expect(pngDimensions("public/brand/maskable-icon-512.png")).toEqual([
      512, 512,
    ]);
    expect(pngDimensions("app/apple-icon.png")).toEqual([180, 180]);
  });

  it("ships a multi-resolution favicon", () => {
    const favicon = readFileSync(
      path.resolve(process.cwd(), "app/favicon.ico"),
    );
    expect(favicon.readUInt16LE(0)).toBe(0);
    expect(favicon.readUInt16LE(2)).toBe(1);
    expect(favicon.readUInt16LE(4)).toBe(3);
    const frameSizes = Array.from({ length: 3 }, (_, index) => {
      const entry = 6 + index * 16;
      return [favicon.readUInt8(entry), favicon.readUInt8(entry + 1)];
    });
    expect(frameSizes).toEqual([
      [16, 16],
      [32, 32],
      [48, 48],
    ]);
  });

  it("generates the Next icon route from the canonical vector mark", () => {
    expect(readFileSync(path.resolve(process.cwd(), "app/icon.svg"))).toEqual(
      readFileSync(path.resolve(process.cwd(), "public/brand-mark.svg")),
    );
  });

  it("keeps the install manifest aligned with generated files", () => {
    expect(manifest()).toMatchObject({
      name: "Bumpa Bestie",
      short_name: "Bestie",
      start_url: "/login",
      theme_color: "#123e31",
      icons: [
        { src: "/brand/app-icon-192.png?v=20260714", sizes: "192x192" },
        { src: "/brand/app-icon-512.png?v=20260714", sizes: "512x512" },
        {
          src: "/brand/maskable-icon-512.png?v=20260714",
          sizes: "512x512",
          purpose: "maskable",
        },
      ],
    });
  });
});
