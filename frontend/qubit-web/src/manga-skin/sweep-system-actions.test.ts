// @vitest-environment node
/**
 * Finish-sweep contract: Manga Skills toggles render at the intended compact
 * 76x38 without being clamped by the shared Sakuga max-width cap, and a
 * collapsed disabled system action shows only the large ImportantTooltip.
 *
 * Everything here is scoped to `html[data-qubit-manga="on"]`; the classic
 * skin keeps its upstream sizing and tooltip behaviour.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const css = read("./sweep-system-actions.css");
const app = read("../App.tsx");

const skillToggleBlock = css.slice(
  css.indexOf(".qm-skill-row sakuga-toggle"),
  css.indexOf("/* The two system actions"),
);

const systemActionButton = app.slice(
  app.indexOf("function SystemActionButton"),
  app.indexOf("function SidebarIconWithTooltip"),
);

describe("Manga Skills toggle sizing", () => {
  it("uses zoom so layout, paint and hit area all shrink to 76x38", () => {
    // 108 * 0.7037 = 76.0 and 54 * 0.7037 = 38.0. zoom shrinks the used
    // layout box; transform would leave a 108px box behind the visual.
    expect(skillToggleBlock).toMatch(/width: 108px !important/);
    expect(skillToggleBlock).toMatch(/max-width: none !important/);
    expect(skillToggleBlock).toMatch(/zoom: 0\.7037037 !important/);
    expect(skillToggleBlock).not.toMatch(/transform: scale\(/);
    expect(skillToggleBlock).toMatch(/margin: 0 !important/);
  });

  it("reserves the same 76px in the row so the control cannot cover the name", () => {
    expect(css).toContain(
      "grid-template-columns: 76px minmax(0, 1fr) 40px !important",
    );
    expect(css).toMatch(
      /\.qm-skill-row > :first-child[\s\S]*?width: 76px !important/,
    );
    expect(css).toMatch(
      /@media \(max-width: 680px\)[\s\S]*?76px minmax\(0, 1fr\) 36px/,
    );
  });

  it("keeps every skill/toggle override inside the Manga scope", () => {
    const scoped = css
      .split("\n")
      .filter((line) => line.includes(".qm-skill-row"))
      .filter((line) => line.trim().startsWith('html[data-qubit-manga="on"]'));
    expect(scoped.length).toBeGreaterThanOrEqual(3);
    expect(css).not.toMatch(/^\.qm-skill-row/m);
    expect(css).not.toMatch(/^sakuga-toggle/m);
  });
});

describe("collapsed system action tooltips", () => {
  it("does not render the small SidebarTooltip when a refusal reason is active", () => {
    expect(systemActionButton).toContain(
      "{collapsed && tooltipAnchor && !item.disabledReason && (",
    );
    expect(systemActionButton).toContain("active={Boolean(item.disabledReason)}");
    expect(systemActionButton).toContain("detail={item.disabledReason}");
  });

  it("never passes the reason to the small SidebarTooltip (one tooltip only)", () => {
    const smallTooltip = systemActionButton.slice(
      systemActionButton.indexOf("<SidebarTooltip"),
      systemActionButton.indexOf("/>", systemActionButton.indexOf("<SidebarTooltip")),
    );
    expect(smallTooltip).not.toContain("detail=");
  });
});
