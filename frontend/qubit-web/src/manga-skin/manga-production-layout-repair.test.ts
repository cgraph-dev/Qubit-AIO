// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const css = read("./manga-production-layout-repair.css");
const skin = read("./MangaSkin.tsx");
const installer = read("../../scripts/install-manga-approved.mjs");
const prepare = read("../../scripts/prepare-manga-zip.mjs");

describe("approved Manga artwork alignment contract", () => {
  it("loads geometry repair after every older Manga stylesheet", () => {
    const approved = skin.indexOf('import "./manga-approved-production.css";');
    const fix = skin.indexOf('import "./manga-production-layout-repair.css";');
    expect(approved).toBeGreaterThanOrEqual(0);
    expect(fix).toBeGreaterThan(approved);
  });

  it("uses one star-free high-resolution plate and a replaceable independent star", () => {
    expect(css).toContain("05_section_titlebar_starless.webp");
    expect(css).toContain("13_star_only.webp");
    expect(css).toContain("aspect-ratio:2104 / 380");
    expect(css).toContain("manga-sidebar-outer-frame.svg");
    expect(css).toContain("manga-tooltip-precision.svg");
    expect(css).toContain("manga-system-title-precision.svg");
    expect(css).toContain(".qm-studio-title-corner__star");
    expect(installer).toContain('"13_star_only.webp"');
    expect(installer).toContain('"05_section_titlebar_starless.webp"');
  });

  it("removes double menu frames and keeps navigation text readable", () => {
    expect(css).toContain(".qagent-sidebar__link::before");
    expect(css).toContain(".qagent-sidebar__link::after");
    expect(css).toContain(".qagent-sidebar__link-label::before");
    expect(css).toContain("text-overflow:ellipsis");
    expect(css).toContain(".qagent-sidebar__section::after");
    expect(css).toContain("content:none!important");
    expect(css).toContain("overflow-y:auto!important");
  });

  it("corrects frame and header sizing without modifying a terminal", () => {
    expect(css).toContain("height:130px");
    expect(css).toContain("height:165px");
    expect(css).toContain("filter:brightness(.65) saturate(.85)");
    expect(css).toContain("01_sidebar_header.webp");
    expect(css).toContain("02_system_panel_frame.webp");
    expect(css).not.toContain("xterm");
    expect(css).not.toContain("/workbench");
    const tooltip = read("../../public/manga-ui/manga-tooltip-precision.svg");
    expect(tooltip).toContain('<circle cx="267" cy="30" r="27"');
    expect(tooltip).toContain("viewBox=\"0 0 320 170\"");
    const frame = read("../../public/manga-ui/manga-sidebar-outer-frame.svg");
    expect(frame).toContain("viewBox=\"0 0 320 1000\"");
  });

  it("prefers repaired assets and never blindly reapplies the old ZIP", () => {
    expect(prepare).toContain("qubit_manga_REPAIRED_v2_assets.zip");
    expect(prepare).toContain("!existsSync(installedManifest)");
    expect(prepare).toContain(".installed-archive-sha256");
  });
});
