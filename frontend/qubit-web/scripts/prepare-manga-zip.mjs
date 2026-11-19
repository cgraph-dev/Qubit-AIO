#!/usr/bin/env node
/** Expand vendored source ZIP into Vite public folder without extra deps.
 * The original file remains authoritative; React controls supply all text.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { inflateRawSync } from 'node:zlib';

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const zipPath = process.env.QUBIT_MANGA_ARCHIVE || join(webRoot, 'assets', 'qubit_manga_web_install.zip');
const outRoot = join(webRoot, 'public', 'manga-ui', 'zip');
const archive = readFileSync(zipPath);
const PREFIX = 'qubit_manga_web_install/';
const ASSETS = PREFIX + 'public/manga-ui/';
const EOF_SIGNATURE = 0x06054b50;
let eof = -1;
for (let p = archive.length - 22; p >= Math.max(0, archive.length - 65557); p--) {
  if (archive.readUInt32LE(p) === EOF_SIGNATURE) { eof = p; break; }
}
if (eof < 0) throw new Error('Invalid Manga ZIP: central directory missing');
const count = archive.readUInt16LE(eof + 10);
let cursor = archive.readUInt32LE(eof + 16);
const extracted = new Map();
for (let index = 0; index < count; index++) {
  if (archive.readUInt32LE(cursor) !== 0x02014b50) throw new Error('Invalid ZIP central entry');
  const flags = archive.readUInt16LE(cursor + 8);
  const compression = archive.readUInt16LE(cursor + 10);
  const zippedLength = archive.readUInt32LE(cursor + 20);
  const expectedLength = archive.readUInt32LE(cursor + 24);
  const nameLength = archive.readUInt16LE(cursor + 28);
  const extraLength = archive.readUInt16LE(cursor + 30);
  const commentLength = archive.readUInt16LE(cursor + 32);
  const localOffset = archive.readUInt32LE(cursor + 42);
  const name = archive.subarray(cursor + 46, cursor + 46 + nameLength).toString('utf8');
  cursor += 46 + nameLength + extraLength + commentLength;
  if (!(name === PREFIX + 'asset-manifest.json' || name === PREFIX + 'palette.json' ||
        (name.startsWith(ASSETS) && /\.(?:svg|png)$/i.test(name)))) continue;
  if (flags & 1) throw new Error(`Encrypted ZIP entry: ${name}`);
  if (archive.readUInt32LE(localOffset) !== 0x04034b50) throw new Error(`Invalid local ZIP entry: ${name}`);
  const dataOffset = localOffset + 30 + archive.readUInt16LE(localOffset + 26) + archive.readUInt16LE(localOffset + 28);
  const compressed = archive.subarray(dataOffset, dataOffset + zippedLength);
  let content;
  if (compression === 0) content = Buffer.from(compressed);
  else if (compression === 8) content = inflateRawSync(compressed);
  else throw new Error(`Unsupported ZIP compression ${compression}: ${name}`);
  if (content.length !== expectedLength) throw new Error(`Truncated ZIP entry: ${name}`);
  extracted.set(name, content);
}
const manifest = JSON.parse(extracted.get(PREFIX + 'asset-manifest.json')?.toString('utf8') || 'null');
if (!Array.isArray(manifest) || manifest.length !== 89) throw new Error('Manga asset manifest must contain exactly 89 families');
const unique = new Set();
for (const { name } of manifest) {
  if (typeof name !== 'string' || !/^[a-z0-9-]+$/.test(name) || unique.has(name)) throw new Error(`Unsafe or duplicated Manga asset name: ${name}`);
  unique.add(name);
  for (const ext of ['svg', 'png']) {
    const key = `${ASSETS}${name}.${ext}`;
    if (!extracted.has(key)) throw new Error(`ZIP asset missing: ${key}`);
  }
}
const assetEntries = [...extracted.entries()].filter(([name]) => name.startsWith(ASSETS));
if (assetEntries.length !== 178) throw new Error(`Unexpected Manga ZIP asset count: ${assetEntries.length}`);
mkdirSync(outRoot, { recursive: true });
for (const [name, content] of assetEntries) {
  writeFileSync(join(outRoot, name.slice(ASSETS.length)), content);
}
writeFileSync(join(outRoot, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
writeFileSync(join(outRoot, 'palette.json'), extracted.get(PREFIX + 'palette.json'));
console.log(`Installed ${unique.size} Manga asset families (${assetEntries.length} SVG/PNG files)`);