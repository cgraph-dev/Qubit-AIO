#!/usr/bin/env node
/** Restore every original Manga ZIP SVG and PNG using Node built-ins only.
 * The exact uploaded archive is vendored as ten text chunks, to meet the
 * repository's maximum new-blob size. SHA-256 binds all chunks to the ZIP.
 * Runs as an npm predev/prebuild/pretest/pretypecheck hook before Vite.
 */
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const sourceDir = resolve(here, "manga-original-zip-parts");
const output = resolve(here, "../public/manga-ui/zip");
const ZIP_SHA256 = "e385c8c5c5b928c84ea8fe88d975e41b65ba903b8ad58f44878f5982feaf68d0";
const SOURCE_PREFIX = "qubit_manga_web_install/";
const ASSET_PREFIX = SOURCE_PREFIX + "public/manga-ui/";
const EXPECTED_FAMILIES = 89;

function unzip(buffer) {
  let eocd = -1;
  for (let p = buffer.length - 22; p >= Math.max(0, buffer.length - 65557); --p) {
    if (buffer.readUInt32LE(p) === 0x06054b50) { eocd = p; break; }
  }
  if (eocd < 0) throw new Error("Corrupt Manga ZIP: no end-of-central-directory");
  const count = buffer.readUInt16LE(eocd + 10);
  let pos = buffer.readUInt32LE(eocd + 16);
  const files = new Map();
  for (let i = 0; i < count; ++i) {
    if (buffer.readUInt32LE(pos) !== 0x02014b50) throw new Error("Invalid central directory");
    const flags = buffer.readUInt16LE(pos + 8);
    const method = buffer.readUInt16LE(pos + 10);
    const size = buffer.readUInt32LE(pos + 20);
    const expandedSize = buffer.readUInt32LE(pos + 24);
    const nameLength = buffer.readUInt16LE(pos + 28);
    const extraLength = buffer.readUInt16LE(pos + 30);
    const commentLength = buffer.readUInt16LE(pos + 32);
    const localPos = buffer.readUInt32LE(pos + 42);
    const name = buffer.subarray(pos + 46, pos + 46 + nameLength).toString("utf8");
    pos += 46 + nameLength + extraLength + commentLength;
    if (!name.startsWith(SOURCE_PREFIX) || name.includes("..") || files.has(name)) {
      throw new Error("Unexpected/duplicate ZIP entry: " + name);
    }
    if (flags & 1 || method !== 8) throw new Error("Encrypted/non-DEFLATE ZIP entry: " + name);
    if (buffer.readUInt32LE(localPos) !== 0x04034b50) throw new Error("Invalid local header: " + name);
    const start = localPos + 30 + buffer.readUInt16LE(localPos + 26) + buffer.readUInt16LE(localPos + 28);
    if (start + size > buffer.length) throw new Error("Truncated ZIP entry: " + name);
    const contents = inflateRawSync(buffer.subarray(start, start + size));
    if (contents.length !== expandedSize) throw new Error("ZIP entry length mismatch: " + name);
    files.set(name, contents);
  }
  return files;
}

export function installOriginalMangaAssets() {
  const parts = [..."ABCDEFGHIJ"].map((letter) => {
    const part = readFileSync(join(sourceDir, "part-" + letter + ".b64"), "utf8").trim();
    if (!/^[A-Za-z0-9+/=]+$/.test(part)) throw new Error("Invalid archive part: " + letter);
    return part;
  });
  const originalZip = Buffer.from(parts.join(""), "base64");
  if (createHash("sha256").update(originalZip).digest("hex") !== ZIP_SHA256) {
    throw new Error("Original Manga ZIP checksum mismatch");
  }
  const files = unzip(originalZip);
  const manifest = JSON.parse(files.get(SOURCE_PREFIX + "asset-manifest.json")?.toString("utf8") ?? "null");
  const palette = JSON.parse(files.get(SOURCE_PREFIX + "palette.json")?.toString("utf8") ?? "null");
  if (!Array.isArray(manifest) || manifest.length !== EXPECTED_FAMILIES || !palette) {
    throw new Error("Missing 89-family original Manga manifest or palette");
  }
  const planned = [];
  const used = new Set();
  for (const entry of manifest) {
    const name = entry?.name;
    if (typeof name !== "string" || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) || used.has(name)) {
      throw new Error("Invalid/duplicate manifest asset: " + name);
    }
    used.add(name);
    for (const ext of [".svg", ".png"]) {
      const original = files.get(ASSET_PREFIX + name + ext);
      if (!original) throw new Error("Missing original artwork: " + name + ext);
      if (ext === ".svg" && (!original.includes(Buffer.from("<svg")) || /<script\b/i.test(original.toString("utf8")))) {
        throw new Error("Unsafe SVG asset: " + name);
      }
      planned.push({ name: name + ext, data: original });
    }
  }
  if (planned.length !== EXPECTED_FAMILIES * 2) throw new Error("Manga asset count mismatch");
  // No writes occur until the entire archive/manifest has been validated.
  mkdirSync(output, { recursive: true });
  for (const item of planned) writeFileSync(join(output, item.name), item.data);
  writeFileSync(join(output, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  writeFileSync(join(output, "palette.json"), JSON.stringify(palette, null, 2) + "\n");
  console.log("Manga ZIP: installed 89 original SVGs + 89 original PNGs");
  return planned.length;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  installOriginalMangaAssets();
}
