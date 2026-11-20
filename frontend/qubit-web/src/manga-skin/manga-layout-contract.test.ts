// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const load = (relativePath: string) =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

describe("Manga shell and inspector design contracts", () => {
  const imports = load("./MangaSkin.tsx");
  const css = load("./manga-title-and-inspector.css");
  const repair = load("./manga-layout-repair.css");
  const app = load("../App.tsx");
  const page = load("../pages/ChatPage.tsx");

  it("loads the reference-compliant title and inspector style last", () => {
    const order = ["manga-v5.css", "manga-rail-reference.css", "manga-ux.css",
      "manga-layout-repair.css", "manga-title-and-inspector.css"];
    const positions = order.map((name) => imports.indexOf(name));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("keeps the current Manga branding on the live sidebar emblem", () => {
    expect(app).toContain('className="qm-brand-emblem"');
    expect(app).toContain("<MangaStudioBrandAtom");
    expect(load("./manga-approved-production.css")).toContain("11_atom_emblem.webp");
  });

  it("gives the real inspector sections their own readable manga frame", () => {
    for (const name of ["qchat__model-panel", "qchat__tools", "qchat__artifacts", "qchat__sessions"]) {
      expect(page).toContain(name);
      expect(css).toContain(name);
    }
    expect(css).toContain("overflow-y:auto");
    expect(css).toContain("manga-corner-top-left.svg");
    expect(css).toContain("manga-corner-bottom-right.svg");
  });

  it("keeps the 56px collapsed sidebar controls in a vertical stack", () => {
    expect(app).toContain('data-collapsed={isDesktopCollapsed}');
    expect(repair).toContain("width:56px!important");
    expect(repair).toContain("grid-template-columns:minmax(0,1fr)");
    expect(repair).toContain("--sk-input-width:100%!important");
  });

  it("removes old microstripes and uses clear star-and-three-stroke plates", () => {
    expect(repair).toContain("qagent-sidebar__brand-row::after");
    expect(load("./manga-panel-integrity.css")).toContain('[data-dashboard-page-header] h1::after');
    expect(css).toContain("manga-impact-star.svg");
    expect(css).toContain("to bottom,");
  });

  it("preserves reduced-motion preferences", () => {
    expect(css).toContain("prefers-reduced-motion:reduce");
    expect(repair).toContain("prefers-reduced-motion:reduce");
  });
});
