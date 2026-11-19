// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
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

  it("uses a vector black cat with blue eyes for Manga only", () => {
    expect(app).toContain('className="qm-brand-emblem"');
    expect(repair).toContain("qubit-cat-atom.svg");
    const logo = "../" + "../public/manga-ui/qubit-cat-atom.svg";
    const path = fileURLToPath(new URL(logo, import.meta.url));
    expect(existsSync(path)).toBe(true);
    const svg = readFileSync(path, "utf8");
    expect(svg).toContain('viewBox="0 0 128 128"');
    expect(svg).toContain("#28b8ff");
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
    expect(repair).toContain('header[role="banner"]::after');
    expect(css).toContain("manga-impact-star.svg");
    expect(css).toContain("to bottom,");
  });

  it("preserves reduced-motion preferences", () => {
    expect(css).toContain("prefers-reduced-motion:reduce");
    expect(repair).toContain("prefers-reduced-motion:reduce");
  });
});
