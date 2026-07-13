// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const stylesheet = readFileSync(
  new URL("../app/globals.css", import.meta.url),
  "utf8",
);

function customProperty(name: string): string {
  const match = stylesheet.match(
    new RegExp(`--${name}\\s*:\\s*(#[0-9a-fA-F]{6})\\s*;`),
  );
  if (!match) throw new Error(`Missing CSS custom property --${name}`);
  return match[1];
}

function relativeLuminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    ?.map((channel) => Number.parseInt(channel, 16) / 255);
  if (!channels || channels.length !== 3) {
    throw new Error(`Expected a six-digit hex color, received ${hex}`);
  }
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(foreground: string, background: string): number {
  const lighter = Math.max(
    relativeLuminance(foreground),
    relativeLuminance(background),
  );
  const darker = Math.min(
    relativeLuminance(foreground),
    relativeLuminance(background),
  );
  return (lighter + 0.05) / (darker + 0.05);
}

describe("accessible design tokens", () => {
  it("uses an AA-compliant semantic coral for step-number text", () => {
    const stepRule = stylesheet.match(/\.step::before\s*\{([^}]*)\}/)?.[1];

    expect(stepRule).toBeDefined();
    expect(stepRule).toMatch(/color:\s*var\(--coral-ink\)\s*;/);
    expect(stylesheet.match(/var\(--coral-ink\)/g)).toHaveLength(1);
    expect(
      contrastRatio(customProperty("coral-ink"), customProperty("paper")),
    ).toBeGreaterThanOrEqual(4.5);
  });
});
