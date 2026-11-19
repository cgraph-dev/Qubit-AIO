// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const read = (relative: string) => readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
const entry = read("./MangaSkin.tsx");
const css = read("./manga-overflow-guards.css");
const audit = read("../../scripts/audit-manga-ui.mjs");
describe("Manga overflow and browser audit safeguards", () => {
 it("loads the Manga-only corrective styling last", () => {
   expect(entry).toContain('import "./manga-overflow-guards.css";');
   expect(entry.indexOf("manga-overflow-guards.css")).toBeGreaterThan(entry.indexOf("manga-semantic-surfaces.css"));
   expect(css).toContain('html[data-qubit-manga="on"]');
 });
 it("constrains collapsed footer and keeps functional buttons reachable", () => {
   expect(css).toContain('data-collapsed="true"');
   expect(css).toContain('max-height:24dvh');
   expect(css).toContain('max-height:26dvh');
   expect(css).toContain("overflow-y:auto");
 });
 it("hides legacy sidebar video only in Manga", () => {
   expect(css).toContain(".qagent-sidebar__backdrop video");
   expect(css).toContain("display:none!important");
 });
 it("only accepts a verified Q6 UI capture and protects sensitive routes", () => {
   expect(audit).toContain('context.addInitScript');
   expect(audit).toContain('manga-theme-not-activated');
   expect(audit).toContain('SKIPPED_SENSITIVE_ROUTE');
   expect(audit).toContain('collapsed-footer-overlap');
   expect(audit).toContain('main-content-missing');
 });
});
