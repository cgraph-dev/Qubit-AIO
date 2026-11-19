// @vitest-environment node
/** Approved static Manga Web Dashboard component/asset contract. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (path: string) =>
  readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");

const css = read("./manga-approved-production.css");
const gate = read("./MangaSkin.tsx");
const installer = read("../../scripts/install-manga-approved.mjs");
const prepare = read("../../scripts/prepare-manga-zip.mjs");
const art = read("./MangaStudioArt.tsx");

describe("approved production Manga dashboard", () => {
  it("loads after legacy styles and is gated to eligible routes", () => {
    const lines = gate.split("\n").filter(line => line.startsWith('import "./manga-'));
    expect(lines.at(-1)).toBe('import "./manga-approved-production.css";');
    expect(gate).toContain('dataset.qmApprovedAssets = "ready"');
    expect(css).toContain('[data-qm-approved-assets="ready"]');
  });
  it("uses a truly star-free plate and a separately replaceable star", () => {
    expect(installer).toContain("05_section_titlebar_starless.webp");
    expect(gate).toContain("05_section_titlebar_starless.webp");
    expect(css).toContain("05_section_titlebar_starless.webp");
    expect(css).toContain('background: url("/manga-ui/approved/13_star_only.webp")');
    expect(art).toContain("qm-studio-title-corner");
    expect(css).not.toContain("05_section_titlebar_clean.webp");
  });
  it("prepares the verified final asset archive when present", () => {
    expect(prepare).toContain("qubit_manga_FINAL_approved_assets.zip");
    expect(prepare).toContain("install-manga-approved.mjs");
    expect(installer).toContain('createHash("sha256").update(data).digest("hex")');
    for (const asset of [
      "01_sidebar_header.webp", "02_system_panel_frame.webp",
      "03_system_titlebar.webp", "04_tooltip_plate.webp",
      "06_primary_button.webp", "07_disabled_button.webp",
      "08_checkbox_idle.webp", "09_checkbox_active.webp",
      "10_impact_strokes.webp", "11_atom_emblem.webp",
      "12_anime_eye_panel.webp", "13_star_only.webp",
      "14_three_gold_bars.webp",
    ]) {
      expect(installer).toContain(asset);
    }
  });
  it("does not add decorative loops to the final static asset pass", () => {
    expect(css).toContain("animation:none!important");
    expect(css).not.toContain(".xterm");
    expect(css).not.toMatch(/#(?:qubit-office|qubit-workbench)/);
  });
});
