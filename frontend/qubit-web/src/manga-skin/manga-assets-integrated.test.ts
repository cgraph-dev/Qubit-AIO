// @vitest-environment node
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const here = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));
const read = (relative: string) => readFileSync(here(relative), "utf8");
const imported = read("./MangaSkin.tsx");
const layer = read("./manga-assets-integrated.css");
const chrome = read("./manga-layout-repair.css");
const routes = read("./manga-store.ts");

describe("Manga V3 asset and layout integration", () => {
  it("does not load stretched SVG navigation plates after the reported regression", () => {
    expect(imported).not.toContain('import "./manga-assets-integrated.css";');
    expect(imported).toContain('import "./manga-rail-reference.css";');
  });
  it("ships all five text-free reference-inspired SVG assets", () => {
    for (const name of ["nav-row-normal","nav-row-hover","nav-row-selected","nav-section-title","corner-yellow"]) {
      const path = here(`../../public/manga-ui/v3-${name}.svg`);
      expect(existsSync(path)).toBe(true);
      const svg = readFileSync(path,"utf8");
      expect(svg).toContain("<svg");
      expect(svg).toContain("viewBox");
      expect(svg).not.toMatch(/<text\b|<foreignObject\b/);
      expect(layer).toContain(`v3-${name}.svg`);
    }
  });
  it("keeps only Workbench and HUD excluded and allows the chat/office host shells", () => {
    for (const p of ["/qubit-workbench","/qubit-hud"]) {
      expect(routes).toContain(`"${p}"`);
    }
    for (const p of ["/chat","/qubit-office","/qubit-office2","/qubit-office3"]) {
      expect(routes).not.toContain(`"${p}"`);
    }
    expect(layer).toContain('html[data-qubit-manga="on"]');
  });
  it("protects mobile and collapsed nav states", () => {
    expect(layer).toContain('data-collapsed="true"');
    expect(layer).toContain('max-width:1023px');
    expect(chrome).toContain('grid-template-columns:minmax(0,1fr)');
    expect(read("../components/LanguageSwitcher.tsx")).toContain('collapsed ? "100%"');
  });
  it("has accessible reduced-motion and visual regression scaffolding", () => {
    expect(layer).toContain("prefers-reduced-motion:reduce");
    const qa = read("../../scripts/audit-manga-ui.mjs");
    expect(qa).toContain("collapsedFooterOverlap");
    expect(qa).toContain("scrollOverflow");
    expect(qa).toContain('"qubit-dashboard-theme","q6"');
  });
});
