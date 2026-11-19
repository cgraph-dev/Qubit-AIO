// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const read = (p: string) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8");
const css = read("./manga-atom-titles.css");
const atom = read("../../public/manga-ui/manga-anime-atom.svg");
const gate = read("./MangaSkin.tsx");
const app = read("../App.tsx");

describe("reference sidebar title plates and anime atom", () => {
  it("draws a symmetric golden atom, not a jagged lightning/star ornament", () => {
    expect(atom).toContain('viewBox="0 0 80 80"');
    expect(atom).toContain('rx="32"');
    expect(atom).toContain('rotate(-35 40 40)');
    expect(atom).toContain('rotate(35 40 40)');
    expect(css).toContain('manga-anime-atom.svg');
    expect(css).not.toContain("neon-lightning.svg");
  });
  it("keeps the star un-clipped and the three charging cells distinct", () => {
    expect(css).toContain("overflow:visible!important;clip-path:none!important");
    expect(app.match(/qm-manga-charge-cells/g)?.length).toBe(2);
    expect(css).toContain("@keyframes qm-atom-cell-charge");
    expect(css).toContain("animation-delay:-.8s");
    expect(css).toContain("animation-delay:-1.6s");
    expect(css).toContain("prefers-reduced-motion:reduce");
  });
  it("overrides only the visual art and keeps the live navigation controls", () => {
    expect(gate.indexOf('manga-atom-titles.css')).toBeGreaterThan(
      gate.indexOf('manga-zip-controls.css')
    );
    expect(app).toContain("onClick={toggleCollapsed}");
    expect(app).toContain("PanelLeftOpen");
    expect(app).toContain("PanelLeftClose");
    expect(read("./manga-fidelity.css")).toContain("v3-nav-row-selected.svg");
  });
});
