#!/usr/bin/env node
/**
 * Stable Manga ZIP preparation entry point for npm predev/prebuild/pretest/
 * pretypecheck. The authoritative uploaded ZIP is stored as ten lossless,
 * checksum-verified text segments to honor the repository's blob-size policy.
 *
 * Do not depend on assets/qubit_manga_web_install.zip: that binary is not
 * shipped. All 89 original SVGs + 89 PNGs are reconstructed automatically.
 */
import { installOriginalMangaAssets } from "./install-manga-svg.mjs";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const installed = installOriginalMangaAssets();
if (installed !== 178) throw new Error("Incomplete Manga archive extraction");

/* The user-approved production WebP pack is optional during CI checkout, but
 * installs automatically on dev/build/test when the archive is present.
 * It contains 14 standalone assets, including a STAR-FREE titleplate and
 * its separately replaceable star. Never synthesize or stretch substitutes. */

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../../..");
const repairedArchive = resolve(root, "qubit_manga_REPAIRED_v2_assets.zip");
const legacyArchive = resolve(root, "qubit_manga_FINAL_approved_assets.zip");
const installedManifest = resolve(here, "../public/manga-ui/approved/MANIFEST.json");
const stampFile = resolve(here, "../public/manga-ui/approved/.installed-archive-sha256");
// Prefer the corrected 2x/high-resolution pack. Never overwrite a manually
// installed repaired pack with the old ZIP merely because predev runs again.
const approvedArchive = existsSync(repairedArchive)
  ? repairedArchive
  : !existsSync(installedManifest) && existsSync(legacyArchive)
    ? legacyArchive
    : null;
if (approvedArchive) {
  const archiveHash = createHash("sha256").update(readFileSync(approvedArchive)).digest("hex");
  const alreadyInstalled = existsSync(installedManifest) && existsSync(stampFile) &&
    readFileSync(stampFile, "utf8").trim() === archiveHash;
  if (!alreadyInstalled) {
    const installedApproved = spawnSync(process.execPath, [
      resolve(here, "install-manga-approved.mjs"), approvedArchive,
    ], { encoding: "utf8", stdio: "inherit" });
    if (installedApproved.error) throw installedApproved.error;
    if (installedApproved.status !== 0) {
      throw new Error("Qubit Manga approved production asset installation failed");
    }
    writeFileSync(stampFile, archiveHash + "\n");
  }
}
