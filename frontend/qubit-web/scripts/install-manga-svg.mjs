#!/usr/bin/env node
/**
 * Restore ALL 89 original SVG assets from the user's qubit_manga_web_install
 * archive, committed as a lossless zlib+base64 JSON bundle.
 *
 * Runs in dev/build/test before Vite resolves public/ assets. PNG fallback
 * originals remain in the separately distributed source ZIP; the dashboard
 * renders the SVG versions at any density.
 *
 * Zero additional dependencies, strictly validates manifest/file names and
 * source hash, preserves other hand-authored manga-ui SVG assets.
 */
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const bundle = resolve(here, "manga-original-svg-bundle.b64");
const output = resolve(here, "../public/manga-ui/zip");
const EXPECTED_SOURCE_HASH = "3c1ddf1e2f216a57cf757e66c74fc95aa8fd446c770bdcad218a91864713a66f";

export function installOriginalMangaSvgs() {
  const compressed = Buffer.from(readFileSync(bundle, "utf8").trim(), "base64");
  const raw = inflateSync(compressed);
  const digest = createHash("sha256").update(raw).digest("hex");
  if (digest !== EXPECTED_SOURCE_HASH) {
    throw new Error("Manga SVG bundle checksum mismatch (corrupt or modified archive)");
  }
  const { svg, manifest, palette } = JSON.parse(raw.toString("utf8"));
  if (!Array.isArray(manifest) || manifest.length !== 89 || typeof svg !== "object") {
    throw new Error("Expected all 89 source Manga assets");
  }
  const names = new Set();
  for (const entry of manifest) {
    const name = entry.name;
    if (typeof name !== "string" || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
      throw new Error(`Invalid asset name: ${name}`);
    }
    if (names.has(name)) throw new Error(`Duplicate Manga asset: ${name}`);
    names.add(name);
    const source = svg[`${name}.svg`];
    if (typeof source !== "string" || !source.includes("<svg") || /<script\b/i.test(source)) {
      throw new Error(`Missing or unsafe SVG: ${name}.svg`);
    }
  }
  if (Object.keys(svg).length !== names.size) {
    throw new Error("Unexpected extra SVG assets not present in the manifest");
  }
  mkdirSync(output, { recursive: true });
  for (const name of names) {
    writeFileSync(join(output, `${name}.svg`), svg[`${name}.svg`], "utf8");
  }
  writeFileSync(join(output, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  writeFileSync(join(output, "palette.json"), JSON.stringify(palette, null, 2) + "\n");
  console.log(`Manga: installed ${names.size} original SVG assets into public/manga-ui/zip`);
  return names.size;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  installOriginalMangaSvgs();
}
