// @vitest-environment node
/**
 * Regression contract for the user-reference Manga dashboard chrome.
 * The ZIP is restored by npm pretest/prebuild, not committed as generated art.
 * These checks guard the exact wiring that older CSS passes broke.
 */
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const css = read("./manga-component-contract.css");
const app = read("../App.tsx");
const skin = read("./MangaSkin.tsx");
const routes = read("./manga-store.ts");
const tip = read("../components/SidebarTooltip.tsx");
const sessions = read("../components/ChatSessionList.tsx");
const workspace = read("../pages/AgentWorkspacePage.tsx");
const kit = read("../../scripts/prepare-manga-zip.mjs");

describe("Qubit Manga sidebar and gateway visual contract", () => {
  it("mounts a real star in both navigation headings and loads the contract last", () => {
    expect((app.match(/className="qm-manga-section-star hidden"/g) ?? []).length).toBe(2);
    expect(css).toContain("/manga-ui/manga-anime-corner-star.svg");
    expect(css).toContain("nav .qagent-sidebar__section-head::before");
    expect(skin.trimEnd().indexOf('import "./manga-precision-repair.css";')).toBeLessThan(
      skin.trimEnd().indexOf('import "./manga-component-contract.css";')
    );
    expect(css).toContain("@keyframes qm-live-charge");
    expect(css).toContain("@keyframes qm-live-star-glint");
  });
  it("keeps the numbered System menu separate from the fixed gateway instrument", () => {
    expect(app).toContain('className="qagent-sidebar__system qm-manga-gateway-shell"');
    expect(app).toContain('className="qagent-sidebar__telemetry qm-manga-gateway-plate"');
    expect(css).toContain('background: url("/manga-ui/zip/sidebar-gateway.svg")');
    expect(css).toContain(".qm-manga-gateway-shell .qagent-sidebar__action::part(button)");
    expect(css).toContain(".qm-manga-gateway-shell > ul > li");
    expect(app).not.toContain('className="qm-manga-system-index"');
  });
  it("uses original ZIP assets for tooltip, dropdown and update notification", () => {
    for (const asset of ["tooltip", "dropdown-panel", "toast-info", "sidebar-gateway"]) {
      expect(css).toContain('/manga-ui/zip/' + asset + '.svg');
    }
    expect(tip).toContain('const manga = document.documentElement.dataset.qubitManga === "on"');
    expect(tip).toContain('...(manga ? {} : {');
    expect(kit).toContain("installOriginalMangaAssets");
  });
  it("restores single native chat search and refresh animations", () => {
    expect(sessions).toContain('className="qm-chat-session-search');
    expect(sessions).toContain('<input\n          type="search"');
    expect(sessions).not.toContain('<Input\n          type="search"');
    expect(sessions).toContain("qm-refresh-icon--spinning");
    expect(workspace).toContain("qm-refresh-icon--spinning");
    expect(css).toContain("qm-refresh-icon--spinning");
  });
  it("keeps Office and Workbench out of scope while allowing /chat chrome", () => {
    for (const route of ["/workbench","/qubit-workbench","/qubit-office","/qubit-office2","/qubit-office3"]) {
      expect(routes).toContain('"' + route + '"');
    }
    expect(routes).not.toContain('"/chat",');
    // No CSS instruction should modify individual terminal glyphs/cursors.
    expect(css).not.toMatch(/\.xterm(?:-screen|-cursor-layer|-text-layer)?\s*[{,]/);
  });
  it("references available crisp SVGs, and the ZIP sprites after npm pretest", () => {
    const assets = [...css.matchAll(/url\(["']?(\/manga-ui\/[^"'()]+\.svg)["']?\)/g)].map(x => x[1]);
    expect(assets.length).toBeGreaterThanOrEqual(7);
    for (const asset of new Set(assets)) {
      const absolute = fileURLToPath(new URL("../../public" + asset, import.meta.url));
      expect(existsSync(absolute), "Missing artwork: " + asset).toBe(true);
      expect(readFileSync(absolute, "utf8")).toContain("<svg");
    }
  });
});
