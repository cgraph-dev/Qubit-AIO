// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  fileURLToPath(new URL("./manga-page-repair.css", import.meta.url)),
  "utf8",
);

describe("Manga config filter row", () => {
  it("paints the selected Sakuga part as the gold plate with a dark offset shadow", () => {
    const block = css.slice(
      css.indexOf("sakuga-button.qm-config-category[data-active=\"true\"]::part(button)"),
    );
    expect(block).toContain("background:var(--qm-page-yellow)!important");
    expect(block).toContain("color:#080615!important");
    expect(block).toContain("font-weight:800!important");
    expect(block).toContain("box-shadow:3px 3px 0 #080615!important");
    expect(block).not.toContain("inset 3px 0 0");
  });

  it("keeps hover, keyboard focus and counts legible on the gold plate", () => {
    expect(css).toMatch(
      /qm-config-category\[data-active="true"\]::part\(button\):hover/,
    );
    expect(css).toMatch(
      /qm-config-category\[data-active="true"\]::part\(button\):focus-visible/,
    );
    expect(css).toContain("outline:2px solid #080615!important");
    expect(css).toMatch(
      /qm-config-category\[data-active="true"\] > span\.tabular-nums/,
    );
  });

  it("styles the category scrollbar and leaves classic untouched", () => {
    expect(css).toContain(
      '.qm-config-categories [class*="overflow-y-auto"]::-webkit-scrollbar-thumb',
    );
    const added = css.slice(
      css.indexOf("/* Selected filter row:"),
      css.indexOf("html[data-qubit-manga=\"on\"] .qm-config-actions {"),
    );
    const selectors = added
      .split("\n")
      .filter((line) => line.trim().endsWith("{"))
      .filter((line) => !line.trim().startsWith("/*"));
    expect(selectors.length).toBeGreaterThan(0);
    for (const selector of selectors) {
      expect(selector).toContain('html[data-qubit-manga="on"]');
    }
  });
});
