#!/usr/bin/env node
/**
 * Install the 14 independently replaceable, user-approved Manga UI images.
 *
 * Run from the repo root:
 *   node frontend/qubit-web/scripts/install-manga-approved.mjs /path/to/qubit_manga_POLISHED_v3_assets.zip
 *
 * Node built-ins only; no Python, no network, no dynamic JS from the archive.
 * Validates every SHA-256 before writing anything. Each image is a separate
 * WebP in public/manga-ui/approved. The star-free plate and star are independent.
 *
 * Idempotent: when the archive manifest matches the installed manifest and all
 * files exist, do NOT overwrite them. This allows replacing just the gold star
 * by hand without an npm predev/prebuild hook undoing that customization.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const archive = resolve(
  process.argv[2] ?? join(repoRoot, "qubit_manga_POLISHED_v3_assets.zip"),
);
const target = resolve(webRoot, "public/manga-ui/approved");
const prefix = "qubit_manga_approved_production/";
const manifestPath = join(target, "MANIFEST.json");
const REQUIRED = new Set([
  "01_sidebar_header.webp", "02_system_panel_frame.webp",
  "03_system_titlebar.webp", "04_tooltip_plate.webp",
  "05_section_titlebar_starless.webp", "06_primary_button.webp",
  "07_disabled_button.webp", "08_checkbox_idle.webp",
  "09_checkbox_active.webp", "10_impact_strokes.webp",
  "11_atom_emblem.webp", "12_anime_eye_panel.webp",
  "13_star_only.webp", "14_three_gold_bars.webp",
]);

function unzip(data) {
  let end = -1;
  for (let i = data.length - 22; i >= Math.max(0, data.length - 65557); --i) {
    if (data.readUInt32LE(i) === 0x06054b50) { end = i; break; }
  }
  if (end < 0) throw new Error("Invalid Manga ZIP: missing central directory");
  const count = data.readUInt16LE(end + 10);
  let offset = data.readUInt32LE(end + 16);
  const entries = new Map();
  for (let i = 0; i < count; ++i) {
    if (data.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error("Invalid Manga ZIP central directory entry");
    }
    const flags = data.readUInt16LE(offset + 8);
    const method = data.readUInt16LE(offset + 10);
    const compressed = data.readUInt32LE(offset + 20);
    const expanded = data.readUInt32LE(offset + 24);
    const nameLength = data.readUInt16LE(offset + 28);
    const extraLength = data.readUInt16LE(offset + 30);
    const commentLength = data.readUInt16LE(offset + 32);
    const localOffset = data.readUInt32LE(offset + 42);
    const name = data.subarray(offset + 46, offset + 46 + nameLength).toString("utf8");
    offset += 46 + nameLength + extraLength + commentLength;
    if (!name.startsWith(prefix) || entries.has(name) ||
        name.includes("..") || name.includes("\\") ||
        !/^[A-Za-z0-9._/-]+$/.test(name)) {
      throw new Error("Unexpected or duplicate Manga ZIP entry: " + name);
    }
    if ((flags & 1) !== 0 || ![0, 8].includes(method)) {
      throw new Error("Unsupported or encrypted Manga ZIP entry: " + name);
    }
    if (data.readUInt32LE(localOffset) !== 0x04034b50) {
      throw new Error("Corrupt Manga ZIP local entry: " + name);
    }
    const localName = data.readUInt16LE(localOffset + 26);
    const localExtra = data.readUInt16LE(localOffset + 28);
    const start = localOffset + 30 + localName + localExtra;
    if (start + compressed > data.length) throw new Error("Truncated Manga ZIP entry");
    const bytes = data.subarray(start, start + compressed);
    const decoded = method === 8 ? inflateRawSync(bytes) : Buffer.from(bytes);
    if (decoded.length !== expanded) throw new Error("Manga ZIP entry size mismatch: " + name);
    entries.set(name, decoded);
  }
  return entries;
}

function install() {
  if (!existsSync(archive)) throw new Error(
    "Missing approved Manga art archive: " + archive + "\n" +
    "Place qubit_manga_POLISHED_v3_assets.zip in the repository root or pass its path.",
  );
  const entries = unzip(readFileSync(archive));
  const manifestBytes = entries.get(prefix + "MANIFEST.json");
  if (!manifestBytes) throw new Error("Approved Manga manifest is missing");
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  if (!manifest || typeof manifest !== "object" ||
      Object.keys(manifest).length !== REQUIRED.size ||
      ![...REQUIRED].every(name => name in manifest)) {
    throw new Error("Approved Manga archive must contain the final 14 separate images");
  }
  const planned = [];
  for (const name of REQUIRED) {
    const data = entries.get(prefix + name);
    const record = manifest[name];
    if (!data || !record || !Number.isInteger(record.bytes) ||
        !/^[a-f0-9]{64}$/.test(record.sha256) ||
        record.bytes !== data.length ||
        createHash("sha256").update(data).digest("hex") !== record.sha256 ||
        data.subarray(0, 4).toString("ascii") !== "RIFF" ||
        data.subarray(8, 12).toString("ascii") !== "WEBP" ||
        data.readUInt32LE(4) + 8 !== data.length) {
      throw new Error("Corrupt approved Manga image or invalid checksum: " + name);
    }
    planned.push([name, data]);
  }

  // If the same version is already installed, do not touch a replaced star.
  if (existsSync(manifestPath) &&
      readFileSync(manifestPath, "utf8").trim() === JSON.stringify(manifest, null, 2) &&
      [...REQUIRED].every(name => existsSync(join(target, name)))) {
    console.log("Qubit Manga: verified approved asset pack already installed (custom star preserved)");
    return;
  }
  mkdirSync(target, { recursive: true });
  for (const [name, data] of planned) writeFileSync(join(target, name), data);
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  console.log("Qubit Manga: installed all 14 separate approved images into " + target);
}

install();
