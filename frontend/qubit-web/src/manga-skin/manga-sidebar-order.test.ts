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
  it("uses index 07 for Manga status and retains 06 for other themes", () => {
    expect(app).toContain('systemSectionIndex={useMangaNavigation ? "07" : "06"}');
    expect(app).toContain("{systemSectionIndex}");
  });
  it("keeps extension/plugin navigation after numbered groups and before status", () => {
    expect(app.indexOf("sidebarSections.map(")).toBeLessThan(app.indexOf("sidebarNav.pluginItems.length > 0"));
    expect(app.indexOf("sidebarNav.pluginItems.length > 0")).toBeLessThan(app.lastIndexOf("<SidebarSystemActions"));
    expect(app).toContain("t.app.pluginNavSection");
  });
});
