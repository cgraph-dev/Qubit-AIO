// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const css = read("./sweep-position.css");
const models = read("../pages/ModelsPage.tsx");
const sessions = read("../pages/SessionsPage.tsx");
const files = read("../pages/FilesPage.tsx");
const logs = read("../pages/LogsPage.tsx");
const analytics = read("../pages/AnalyticsPage.tsx");
const skills = read("../pages/SkillsPage.tsx");
const openViking = read("../pages/OpenVikingPage.tsx");

describe("Manga page-header action clusters", () => {
  it("marks the Models period/refresh cluster without changing its behaviour", () => {
    expect(models).toContain("qm-header-periods flex flex-wrap items-center gap-1.5");
    expect(models).toContain('aria-label={t.common.refresh}');
  });

  it("marks the Sessions count badge", () => {
    expect(sessions).toContain('className="qm-session-count text-xs tabular-nums"');
  });

  it("puts other header meta text on the shared chip path", () => {
    expect(files).toContain("qm-header-chip qm-header-chip--path");
    expect(files).toContain("title={headerPath}");
    expect(logs).toContain('<Badge tone="secondary" className="qm-header-chip qm-logs-meta text-xs">');
    expect(skills).toContain('className="qm-header-chip flex items-center gap-2');
    expect(openViking).toContain('className="qm-header-chip"');
  });

  it("marks the Analytics period cluster like Models", () => {
    expect(analytics).toContain("qm-header-periods flex flex-wrap items-center gap-1.5");
  });

  it("scopes every new rule to the Manga attribute", () => {
    for (const selector of [
      ".qm-header-periods {",
      ".qm-header-periods sakuga-button::part(button)",
      ".qm-session-count {",
      ".qm-header-chip {",
    ]) {
      const at = css.indexOf(selector);
      expect(at).toBeGreaterThanOrEqual(0);
      const lineStart = css.lastIndexOf("\n", at) + 1;
      expect(css.slice(lineStart, at)).toContain('html[data-qubit-manga="on"]');
    }
    expect(css).not.toMatch(/^\.qm-header-periods\b/m);
    expect(css).not.toMatch(/^\.qm-session-count\b/m);
    expect(css).not.toMatch(/^\.qm-header-chip\b/m);
  });

  it("compacts the period/refresh controls and insets the meta slot", () => {
    expect(css).toContain(".qubit-module-header__meta:has(.qm-header-periods)");
    expect(css).toContain(".qubit-module-header__meta:has(.qm-session-count)");
    expect(css).toContain("padding-inline-end: 10px");
    expect(css).toContain("height: 24px !important");
    expect(css).toContain("font: 700 10px/1");
    expect(css).toContain("width: 13px !important");
    expect(css).toContain("margin-inline-end: 3px");
  });

  it("ellipsizes long chip values inside the Manga plate", () => {
    expect(css).toContain("max-width: min(22rem, 100%)");
    expect(css).toContain("text-overflow: ellipsis !important");
    expect(css).toContain(".qubit-module-header__meta:has(.qm-header-chip)");
    expect(css).toContain("overflow: hidden !important");
    expect(css).toContain(".qm-header-chip--path");
  });
});
