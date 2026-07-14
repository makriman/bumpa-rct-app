import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const publicBrand = path.join(root, "public", "brand");
const appDir = path.join(root, "app");
const markPath = path.join(root, "public", "brand-mark.svg");
const forest = "#123e31";
const lime = "#dbea74";
const paper = "#f8f5ed";
const ink = "#19231f";

await mkdir(publicBrand, { recursive: true });

const mark = await readFile(markPath);
const chatPath =
  "M172.29 68.9A84 84 0 0 0 12 104v64a20 20 0 0 0 20 20h52.1A84.18 84.18 0 0 0 160 236h64a20 20 0 0 0 20-20v-64a84 84 0 0 0-71.71-83.1ZM36 104a60 60 0 1 1 60 60H36Zm184 108h-60a60.14 60.14 0 0 1-49-25.37 83.93 83.93 0 0 0 68.55-91.37A60 60 0 0 1 220 152Z";

function appIconSvg(size) {
  return Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 256 256">
    <rect width="256" height="256" fill="${forest}"/>
    <path fill="${lime}" d="${chatPath}" transform="translate(36 36) scale(.72)"/>
  </svg>`);
}

async function pngFromAppIcon(size) {
  return sharp(appIconSvg(size)).png({ compressionLevel: 9 }).toBuffer();
}

// public/brand-mark.svg is the canonical vector source. Next's file-based icon
// route is generated from it so the browser icon cannot drift from the product
// lockup and raster asset family.
await writeFile(path.join(appDir, "icon.svg"), mark);

await writeFile(
  path.join(publicBrand, "app-icon-192.png"),
  await pngFromAppIcon(192),
);
await writeFile(
  path.join(publicBrand, "app-icon-512.png"),
  await pngFromAppIcon(512),
);
await writeFile(
  path.join(publicBrand, "maskable-icon-512.png"),
  await pngFromAppIcon(512),
);
await writeFile(path.join(appDir, "apple-icon.png"), await pngFromAppIcon(180));

const markData = `data:image/svg+xml;base64,${mark.toString("base64")}`;
const socialCard =
  Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="${paper}"/>
  <rect x="828" y="44" width="328" height="542" rx="48" fill="${forest}"/>
  <image href="${markData}" x="82" y="65" width="64" height="64"/>
  <text x="166" y="108" fill="${ink}" font-family="Inter, Arial, sans-serif" font-size="28" font-weight="800" letter-spacing="-1">Bumpa</text>
  <text x="262" y="108" fill="${forest}" font-family="Georgia, serif" font-size="30" font-style="italic" font-weight="700" letter-spacing="-1">Bestie</text>
  <text x="82" y="258" fill="${ink}" font-family="Georgia, serif" font-size="67" font-weight="700" letter-spacing="-2.2">Know your business.</text>
  <text x="82" y="336" fill="${forest}" font-family="Georgia, serif" font-size="67" font-style="italic" font-weight="700" letter-spacing="-2.2">Move with confidence.</text>
  <text x="84" y="420" fill="#4c5b54" font-family="Inter, Arial, sans-serif" font-size="25">Clear answers from your live store data.</text>
  <text x="84" y="465" fill="#4c5b54" font-family="Inter, Arial, sans-serif" font-size="25">Practical next steps, in plain language.</text>
  <path fill="${lime}" d="${chatPath}" transform="translate(855 103) scale(1.02)"/>
  <text x="882" y="462" fill="#ffffff" font-family="Inter, Arial, sans-serif" font-size="26" font-weight="700">Your data.</text>
  <text x="882" y="498" fill="${lime}" font-family="Georgia, serif" font-size="30" font-style="italic" font-weight="700">Your next move.</text>
  <text x="84" y="566" fill="${forest}" font-family="Inter, Arial, sans-serif" font-size="20" font-weight="700" letter-spacing="1.5">BUMPABESTIE.COM</text>
</svg>`);

await sharp(socialCard)
  .png({ compressionLevel: 9 })
  .toFile(path.join(publicBrand, "social-card.png"));

// Keep the ICO focused on the traditional browser sizes. The larger install
// surfaces use the dedicated PNG assets above rather than upscaling a favicon.
const faviconSizes = [16, 32, 48];
const faviconImages = await Promise.all(
  faviconSizes.map((size) =>
    sharp(mark).resize(size, size).png({ compressionLevel: 9 }).toBuffer(),
  ),
);
const header = Buffer.alloc(6 + faviconSizes.length * 16);
header.writeUInt16LE(0, 0);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(faviconSizes.length, 4);
let offset = header.length;
faviconSizes.forEach((size, index) => {
  const entry = 6 + index * 16;
  header.writeUInt8(size, entry);
  header.writeUInt8(size, entry + 1);
  header.writeUInt8(0, entry + 2);
  header.writeUInt8(0, entry + 3);
  header.writeUInt16LE(1, entry + 4);
  header.writeUInt16LE(32, entry + 6);
  header.writeUInt32LE(faviconImages[index].length, entry + 8);
  header.writeUInt32LE(offset, entry + 12);
  offset += faviconImages[index].length;
});
await writeFile(
  path.join(appDir, "favicon.ico"),
  Buffer.concat([header, ...faviconImages]),
);
