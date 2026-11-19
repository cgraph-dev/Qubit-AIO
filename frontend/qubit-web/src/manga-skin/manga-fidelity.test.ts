// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (file: string) => readFileSync(fileURLToPath(new URL(file, import.meta.url)), "utf8");
const css = read("./manga-fidelity.css");
const source = read("../App.tsx");
const footer = read("../components/SidebarFooter.tsx");
const gate = read("./MangaSkin.tsx");

describe("Manga reference fidelity pass", () => {
  it("mounts after previous skin styles and only addresses enabled Manga", () => {
    expect(gate.indexOf('manga-fidelity.css')).toBeGreaterThan(gate.indexOf('manga-neon-reference.css'));
    expect(css).toContain('html[data-qubit-manga="on"] #app-sidebar');
    expect(css).toContain('#app-sidebar:not([data-collapsed="true"])');
  });
  it("provides an illustrated cat and two original cut-corner nav assets", () => {
    const cat = read("../../public/manga-ui/qubit-cat-atom.svg");
    const normal = read("../../public/manga-ui/v3-nav-row-normal.svg");
    const hover = read("../../public/manga-ui/v3-nav-row-hover.svg");
    expect(cat).toContain('id="eye"');
    expect(cat).toContain('id="orbit"');
    expect(cat).toContain('id="star"');
    for (const asset of [normal, hover]) {
      expect(asset).toContain('id="dots"');
      expect(asset).toContain('clipPath');
    }
  });
  it("retains the real collapse/expand behavior and yellow selected-row transition", () => {
    expect(source).toContain("onClick={toggleCollapsed}");
    expect(source).toContain("PanelLeftClose");
    expect(source).toContain("PanelLeftOpen");
    expect(css).toContain('v3-nav-row-selected.svg');
    expect(css).toContain('.qagent-sidebar__link[aria-current="page"]');
    expect(css).toContain("clip-path:none!important");
    expect(css).not.toContain('pointer-events:auto');
  });
  it("has independently staggered charging cells and footer LEDs", () => {
    expect(source.match(/qm-manga-charge-cells/g)?.length).toBe(2);
    expect(footer).toContain("qm-manga-footer-leds");
    expect(css).toContain("@keyframes qm-f-cell-charge");
    expect(css).toContain("@keyframes qm-f-led-pulse");
    expect(css).toContain("nth-child(2)");
    expect(css).toContain("nth-child(3)");
    expect(css).toContain("@media(prefers-reduced-motion:reduce)");
    expect(css).toContain("animation:none!important");
  });
});
