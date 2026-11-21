// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const css = read("./sweep-position.css");
const models = read("../pages/ModelsPage.tsx");
const sessions = read("../pages/SessionsPage.tsx");

describe("Manga page-header action clusters", () => {
  it("marks the Models period/refresh cluster without changing its behaviour", () => {
    expect(models).toContain("qm-header-periods flex flex-wrap items-center gap-1.5");
    expect(models).toContain('aria-label={t.common.refresh}');
  });

  it("marks the Sessions count badge", () => {
    expect(sessions).toContain('className="qm-session-count text-xs tabular-nums"');
  });

  it("scopes every new rule to the Manga attribute", () => {
    for (const selector of [
      ".qm-header-periods {",
      ".qm-header-periods sakuga-button::part(button)",
      ".qm-session-count {",
    ]) {
      const at = css.indexOf(selector);
      expect(at).toBeGreaterThanOrEqual(0);
      const lineStart = css.lastIndexOf("\n", at) + 1;
      expect(css.slice(lineStart, at)).toContain('html[data-qubit-manga="on"]');
    }
    expect(css).not.toMatch(/^\.qm-header-periods\b/m);
    expect(css).not.toMatch(/^\.qm-session-count\b/m);
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
});
