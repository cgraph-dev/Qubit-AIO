// @vitest-environment node
/**
 * Sweep lane contract: the sidebar menu tooltip uses the small ink plate with
 * a contained title, a clear body field, and the Sakuga open cue. The large
 * Q5T plate stays reserved for ImportantTooltip (blocked/safety actions).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const css = read("./sweep-tooltips.css");
const skin = read("./MangaSkin.tsx");
const tip = read("../components/SidebarTooltip.tsx");
const important = read("../components/ImportantTooltip.tsx");

describe("Manga sidebar menu tooltip", () => {
  it("uses the small menu plate at high specificity and keeps the Q5T plate out", () => {
    expect(css).toContain('/manga-ui/sidebar-menu-tooltip.svg');
    expect(css).toContain('body > .manga-rail-tooltip');
    expect(css).not.toContain("q5t");
    expect(important).toContain("Q5TTooltipPlate");
    expect(skin).toContain('import "./sweep-tooltips.css";');
  });

  it("contains the title in the tab and starts the body below it", () => {
    expect(css).toMatch(/manga-rail-tooltip__title[\s\S]*?width: 68px/);
    expect(css).toMatch(/manga-rail-tooltip__title[\s\S]*?min-height: 29px/);
    expect(css).toMatch(/manga-rail-tooltip__body[\s\S]*?margin: 7px 70px 0 30px/);
  });

  it("gives title and body a visible shadow", () => {
    const title = css.slice(css.indexOf(".manga-rail-tooltip__title"));
    const body = css.slice(css.indexOf(".manga-rail-tooltip__body"));
    expect(title).toMatch(/text-shadow:[^;]*0 1px 3px/);
    expect(body).toMatch(/text-shadow:[^;]*0 1px 3px/);
  });

  it("keeps the title yellow and both faces legible inside the plate", () => {
    const title = css.slice(css.indexOf(".manga-rail-tooltip__title"));
    const body = css.slice(css.indexOf(".manga-rail-tooltip__body"));
    expect(title).toMatch(/color: #ffe500/);
    expect(title).toMatch(/font: 800 11px\/1\.12/);
    expect(body).toMatch(/font: 600 12\.5px\/1\.38/);
  });

  it("fires the Sakuga open cue from the trigger once, skipping warm/reduced motion", () => {
    expect(tip).toContain('playLocalFx("pop", trigger');
    expect(tip).toContain("fxRef");
    expect(tip).toContain("prefers-reduced-motion: reduce");
    expect(tip).toContain("document.hidden");
    expect(tip).toContain("Date.now() - warmRef.current < 300");
  });
});
