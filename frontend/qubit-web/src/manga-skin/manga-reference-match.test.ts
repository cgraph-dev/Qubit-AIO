// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const read = (p: string) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8");
const css = read("./manga-reference-match.css");
const gate = read("./MangaSkin.tsx");
const store = read("./manga-store.ts");
describe("PR11 reference matching contract", () => {
 it("mounts as the final Q6-only visual layer", () => {
  expect(gate).toContain('import "./manga-reference-match.css";');
  expect(gate.indexOf('manga-reference-match.css')).toBeGreaterThan(gate.indexOf('manga-studio-pass.css'));
  expect(css).toContain('html[data-qubit-manga="on"]');
 });
 it("does not target excluded routes or their components", () => {
  for (const path of ['"/chat"','"/qubit-workbench"','"/workbench"']) expect(store).toContain(path);
  expect(store).toContain('pathname.startsWith(path + "/")');
  expect(css).not.toContain("qubit-workbench");
  expect(css).not.toContain("qubit-office");
 });
 it("uses corner illustrations, not stretched full SVG navigation frames", () => {
  expect(css).toContain("v3-original-ornament-corner.svg");
  expect(css).toContain("v3-original-corner-pink.svg");
  expect(css).not.toContain("sidebar-expanded.svg");
  expect(css).not.toContain("v3-nav-row-normal.svg");
  expect(css).toContain("pointer-events:none");
 });
 it("keeps short one-shot motion and honors reduced-motion preferences", () => {
  expect(css).toContain("prefers-reduced-motion:reduce");
  expect(css).toContain("translate3d");
  expect(css).not.toContain("animation-iteration-count:infinite");
 });
});
