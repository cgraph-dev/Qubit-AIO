// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
const css = read("./manga-neon-reference.css");
const gate = read("./MangaSkin.tsx");
const app = read("../App.tsx");

describe("neon manga sidebar reference", () => {
  it("gates the new styling to Q6/Q6-L and imports it after the established layers", () => {
    expect(css).toContain('html[data-qubit-manga="on"]');
    expect(gate.indexOf('manga-neon-reference.css')).toBeGreaterThan(gate.indexOf('manga-overflow-guards.css'));
  });

  it("preserves the live sidebar collapse button, navigation and selected image", () => {
    expect(app).toContain("onClick={toggleCollapsed}");
    expect(app).toContain("PanelLeftClose");
    expect(app).toContain("PanelLeftOpen");
    expect(app).toContain("sectionIndex > 0 || useMangaNavigation");
    expect(css).toContain('.qagent-sidebar__link[aria-current="page"]');
    expect(css).toContain('v3-nav-row-selected.svg');
    expect(css).not.toContain("pointer-events:auto");
  });

  it("ships the three required vector ornaments", () => {
    for (const name of ["neon-spark.svg", "neon-lightning.svg", "neon-bolt-nut.svg"]) {
      const asset = read("../../public/manga-ui/" + name);
      expect(asset).toContain("<svg");
      expect(css).toContain(name);
    }
  });

  it("animates sparkles and charging stripes without ignoring reduced-motion", () => {
    expect(css).toContain("@keyframes qm-neon-twinkle");
    expect(css).toContain("@keyframes qm-neon-charge");
    expect(css).toContain("@keyframes qm-neon-led");
    expect(css).toContain("@media (prefers-reduced-motion:reduce)");
    expect(css).toContain("animation:none!important");
  });
});
