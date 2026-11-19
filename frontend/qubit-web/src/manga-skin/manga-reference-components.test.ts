// @vitest-environment node
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const path = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const read = (p: string) => readFileSync(path(p), "utf8");
const css = read("./manga-reference-components.css");
const gate = read("./MangaSkin.tsx");

describe("reference-inspired Manga artwork integration", () => {
 it("ships four independent scalable SVG ornaments without embedded text", () => {
  for (const name of ["angle-tab","impact-corner","corner-tip","three-strokes"]) {
   const asset = "../../public/manga-ui/manga-reference-" + name + ".svg";
   expect(existsSync(path(asset))).toBe(true);
   const svg = read(asset);
   expect(svg).toContain("<svg");
   expect(svg).toContain("viewBox=");
   expect(svg).not.toMatch(/<text\b|<foreignObject\b|<image\b/);
   expect(css).toContain("manga-reference-" + name + ".svg");
  }
 });
 it("preserves functional live links rather than fixed-size nav screenshots", () => {
  expect(css).toContain('#app-sidebar:not([data-collapsed="true"])');
  expect(css).not.toContain("background-size:100% 100%");
  expect(css).not.toContain("v3-nav-row-normal.svg");
  expect(gate).not.toContain('import "./manga-assets-integrated.css"');
 });
 it("stays behind Q6/Q6-L route gate and honors reduced motion", () => {
  expect(css).toContain('html[data-qubit-manga="on"]');
  expect(gate).toContain('import "./manga-reference-components.css";');
  expect(css).toContain("prefers-reduced-motion:reduce");
  expect(css).toContain('data-collapsed="true"');
 });
 it("reuses star icon without removing live heading text or Sakuga actions", () => {
  expect(css).toContain("manga-impact-star.svg");
  expect(css).toContain(".qm-confirm-dialog");
  expect(css).toContain(".qchat__side");
  expect(css).toContain("pointer-events:none");
 });
});
