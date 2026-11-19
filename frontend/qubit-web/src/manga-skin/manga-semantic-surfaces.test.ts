// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const read = (path: string) => readFileSync(fileURLToPath(new URL(path,import.meta.url)),"utf8");
const shared = read("../components/console-ui.tsx");
const css = read("./manga-semantic-surfaces.css");
const gate = read("./MangaSkin.tsx");
describe("shared Manga panel anatomy", () => {
 it("provides explicit shared semantics without modifying control logic", () => {
  for (const surface of ["page-header","settings-section","panel","row","empty","loading"]) {
   expect(shared).toContain(`data-qm-surface="${surface}"`);
   expect(css).toContain(`data-qm-surface="${surface}"`);
  }
 });
 it("keeps Manga styling route-scoped without the stretched V3 plate import", () => {
  expect(gate).not.toContain('import "./manga-assets-integrated.css";');
  expect(gate).toContain('import "./manga-semantic-surfaces.css";');
  expect(css).toContain('html[data-qubit-manga="on"]');
 });
 it("preserves live controls and accessible messages", () => {
  expect(shared).toContain("aria-busy");
  expect(shared).toContain("settings-row__control");
  expect(css).not.toMatch(/pointer-events:\s*none[^;]*;\s*[^}]*button/);
  expect(css).toContain("prefers-reduced-motion:reduce");
 });
});
