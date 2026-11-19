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
import { existsSync } from "node:fs";
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
const approvedArchive = resolve(root, "qubit_manga_FINAL_approved_assets.zip");
if (existsSync(approvedArchive)) {
  const installedApproved = spawnSync(process.execPath, [
    resolve(here, "install-manga-approved.mjs"), approvedArchive,
  ], { encoding: "utf8", stdio: "inherit" });
  if (installedApproved.error) throw installedApproved.error;
  if (installedApproved.status !== 0) {
    throw new Error("Qubit Manga approved production asset installation failed");
  }
}
