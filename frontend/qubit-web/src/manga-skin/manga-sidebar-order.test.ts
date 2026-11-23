// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { MANGA_SIDEBAR_SECTIONS } from "../lib/sidebar-sections";
const app = readFileSync(fileURLToPath(new URL("../App.tsx", import.meta.url)), "utf8");

describe("Manga sidebar section numbering and plugin position", () => {
  it("keeps the actual route groups in numbered sequence", () => {
    expect(MANGA_SIDEBAR_SECTIONS.map(({ index, label }) => [index, label])).toEqual([
      ["01", "Chat"], ["02", "Workspace"], ["03", "Automations"],
      ["04", "AI Tools"], ["05", "Connections"],
      ["06", "Voice & Media"], ["07", "System"],
    ]);
  });
  it("does not repeat a section index in the compact System panel", () => {
    expect(app).not.toContain("systemSectionIndex");
  });
  it("shows the Manga Chat and Workspace labels instead of translated Primary", () => {
    expect(app).toMatch(/label: useMangaNavigation\s*\? group\.section\.label\s*:/);
    expect(app).toContain("aria-label={label}");
    expect(app).toContain("label={label.toUpperCase()}");
  });
  it("keeps extension/plugin navigation after numbered groups and before status", () => {
    expect(app.indexOf("sidebarSections.map(")).toBeLessThan(app.indexOf("sidebarNav.pluginItems.length > 0"));
    expect(app.indexOf("sidebarNav.pluginItems.length > 0")).toBeLessThan(app.lastIndexOf("<SidebarSystemActions"));
    expect(app).toContain("t.app.pluginNavSection");
  });
});
