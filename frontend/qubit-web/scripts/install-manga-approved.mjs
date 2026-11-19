#!/usr/bin/env node
/**
 * One-time installer for the user-approved 14-piece production artwork pack.
 *
 * Usage from repo root:
 *   node frontend/qubit-web/scripts/install-manga-approved.mjs /path/to/qubit_manga_approved_production_assets.zip
 *
 * The installer NEVER changes React, the ZIP, the default theme or any other
 * route. It extracts only checksum-verified artwork to public/manga-ui/approved.
 * Local files are intentionally not committed as incomplete image approximations.
 */
import { existsSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "../..");
const archive = resolve(process.argv[2] ?? join(repoRoot, "qubit_manga_approved_production_assets.zip"));
if (!existsSync(archive)) {
  console.error("Missing approved art ZIP:", archive);
  console.error("Usage: node frontend/qubit-web/scripts/install-manga-approved.mjs /path/to/qubit_manga_approved_production_assets.zip");
  process.exit(1);
}

const python = String.raw`
from pathlib import Path
import hashlib, json, sys, zipfile

archive, target = Path(sys.argv[1]), Path(sys.argv[2])
prefix = "qubit_manga_approved_production/"
with zipfile.ZipFile(archive) as z:
    manifest = json.loads(z.read(prefix + "MANIFEST.json"))
    required = {
        "01_sidebar_header.webp", "02_system_panel_frame.webp",
        "03_system_titlebar.webp", "04_tooltip_plate.webp",
        "05_section_titlebar_clean.webp", "06_primary_button.webp",
        "07_disabled_button.webp", "08_checkbox_idle.webp",
        "09_checkbox_active.webp", "10_impact_strokes.webp",
        "11_atom_emblem.webp", "12_anime_eye_panel.webp",
        "13_star_only.webp", "14_three_gold_bars.webp",
    }
    missing = required - manifest.keys()
    if missing: raise SystemExit("Approved ZIP missing: " + ", ".join(sorted(missing)))
    # Verify EVERY blob before writing ANY file.
    contents = {}
    for name, metadata in manifest.items():
        if "/" in name or "\\\" in name or name.startswith("."):
            raise SystemExit("Disallowed filename: " + name)
        data = z.read(prefix + name)
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise SystemExit("Checksum mismatch: " + name)
        contents[name] = data
    target.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        (target / name).write_bytes(data)
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print("Installed", len(contents), "approved Qubit Manga assets into", target)
`;
const result = spawnSync("python3", [
  "-c", python, archive, resolve(webRoot, "public/manga-ui/approved"),
], { encoding: "utf8", stdio: "inherit" });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
