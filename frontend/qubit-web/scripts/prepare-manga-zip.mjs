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

const installed = installOriginalMangaAssets();
if (installed !== 178) throw new Error("Incomplete Manga archive extraction");
