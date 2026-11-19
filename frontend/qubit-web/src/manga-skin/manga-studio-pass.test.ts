// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const read = (p: string) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8");
const css = read("./manga-studio-pass.css");
const gate = read("./MangaSkin.tsx");
const store = read("./manga-store.ts");

describe("Manga studio styling contract", () => {
  it("is opt-in and never loads for /chat or /qubit-workbench", () => {
    expect(gate).toContain('import "./manga-studio-pass.css";');
    expect(css).not.toMatch(/(^|\n)\s*(?:body|:root|#app-sidebar)\s*\{/);
    expect(css).toContain('html[data-qubit-manga="on"]');
    expect(store).toContain('"/chat"');
    expect(store).toContain('"/qubit-workbench"');
    expect(store).toContain('"/workbench"');
    expect(store).toContain('pathname.startsWith(path + "/")');
  });
  it("uses original install-package colors without fixed-size row artwork", () => {
    for (const color of ["#0A0A1F","#171027","#241334","#FF269A","#FF0F85","#B965FF","#FFF0FF","#FFE500","#27E9F3"]) {
      expect(css.toUpperCase()).toContain(color.toUpperCase());
    }
    expect(css).not.toContain("v3-nav-row-normal.svg");
    expect(css).not.toContain("background-size:100% 100%");
  });
  it("keeps ornament hit boxes noninteractive and limits movement to composited properties", () => {
    expect(css).toContain("pointer-events:none");
    expect(css).toContain("@media (prefers-reduced-motion:reduce)");
    expect(css).toContain("translate3d");
    expect(css).not.toMatch(/@keyframes[^}]+(?:width|height|top|left):/);
  });
});
