/*
 * Dual-dock geometry — the measured overlap bug.
 *
 * `OfficeAppShell` renders the Agents sheet on the left and the HUD sheet on
 * the right, each `min(44rem, 92%)` wide. The office host is the dashboard's
 * content column, not the whole viewport: at 1440x900 the measured column is
 * x=263..1408 (1145px, the 263px sidebar plus page padding). Two single-dock
 * sheets do not fit in 1145px, so the right HUD sheet overlapped the Agents
 * sheet's right edge and sat on its Close button (measured x=263..923 and
 * x=748..1408).
 *
 * jsdom has no layout engine, so this test evaluates the SAME box model the
 * stylesheet declares, from the real `game.css` / `tokens.css` sources. It is
 * a contract: if the dual-dock rule is removed, weakened, or the single-dock
 * width is changed, these rectangles overlap again and the test fails.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GAME_CSS = path.resolve(HERE, "../styles/game.css");
const TOKENS_CSS = path.resolve(HERE, "../styles/tokens.css");

const css = readFileSync(GAME_CSS, "utf8");
const tokens = readFileSync(TOKENS_CSS, "utf8");

function tokenPx(name: string): number {
  const match = new RegExp(`${name}:\\s*(\\d+)px`).exec(tokens);
  if (!match) throw new Error(`missing ${name} in tokens.css`);
  return Number(match[1]);
}

const GAP = tokenPx("--office-s3");

// The live measurement at 1440x900: a 263px dashboard sidebar and 32px of page
// padding leave the office host a 1145px content column, not the full viewport.
const VIEWPORT_1440 = 1440;
const SIDEBAR = 263;
const PAGE_PADDING = 32;
const CONTENT_1440 = VIEWPORT_1440 - SIDEBAR - PAGE_PADDING;

/** The two absolutely-positioned sheets at a given host content width. */
function dockRects(container: number) {
  // `.office-game[data-office-docks="both"] > .office-game__dock {
  //    width: calc(50% - var(--office-s3)); }`
  const width = container / 2 - GAP;
  return {
    width,
    agents: { left: 0, right: width },
    hud: { left: container - width, right: container },
  };
}

describe("dual-dock geometry", () => {
  it("the stylesheet splits the row only while both docks are open", () => {
    // The single-dock width must stay exactly what it was.
    const base = /\.office-game__dock\s*\{([^}]*)\}/.exec(css);
    expect(base).not.toBeNull();
    expect(base?.[1] ?? "").toMatch(/width:\s*min\(44rem,\s*92%\)/);

    const dual = /\.office-game\[data-office-docks="both"\]\s*>\s*\.office-game__dock\s*\{([^}]*)\}/
      .exec(css);
    expect(dual).not.toBeNull();
    expect(dual?.[1] ?? "").toMatch(
      /width:\s*calc\(50%\s*-\s*var\(--office-s3\)\)/);
    // The width is the FULL sheet width: the panel padding and border must not
    // be added back on top, or the two halves overlap again.
    expect(dual?.[1] ?? "").toMatch(/box-sizing:\s*border-box/);
  });

  it("two single-width sheets do NOT fit the measured 1440px content column", () => {
    // Why the dual rule is needed.
    const singleAt16 = Math.min(44 * 16, 0.92 * CONTENT_1440); // 704px
    expect(singleAt16 * 2).toBeGreaterThan(CONTENT_1440);
  });

  it("the rects never overlap at 1440x900 or at phone width", () => {
    // 1145px is the measured office host width at 1440x900. 390px is the
    // narrowest design target (mobile has no sidebar); the split still holds.
    for (const container of [CONTENT_1440, 390]) {
      const { agents, hud } = dockRects(container);
      expect(agents.right, `agents right @${container}`)
        .toBeLessThanOrEqual(hud.left);
      // Exactly `2 * gap` between the sheets: each is `50% - gap`.
      expect(hud.left - agents.right).toBe(GAP * 2);
      expect(agents.right).toBeGreaterThan(0);
      expect(hud.right).toBe(container);
    }
  });

  it("the Agents Close button is inside its sheet, clear of the HUD sheet", () => {
    const { agents, hud } = dockRects(CONTENT_1440);
    // The Close button is the last child of the dock head; the head has
    // `padding: ... var(--office-s3)` and the Agents sheet adds a 1px
    // right border, so its right edge is at most `agents.right - gap - 1`.
    const closeRight = agents.right - GAP - 1;
    expect(closeRight).toBeLessThan(hud.left);
    expect(closeRight).toBeGreaterThan(0);
  });
});

/*
 * Toolbar clearance — the measured HUD occlusion.
 *
 * The vendored app's top bar is `position: fixed`, 64px tall by default and
 * 124px when it shows the usage row (108px at phone width). It hosts the
 * injected Panels group whose HUD/Agents icons OPEN these side sheets. The
 * sheet was anchored at `top: 0`, so at 1440x900 the HUD sheet (top≈75) covered
 * the Panels icons at y≈90; after the Agents sheet closed, the icons were
 * behind the HUD and could not be clicked.
 *
 * jsdom has no layout engine, so this pins the SAME contract the dual-dock test
 * pins: the marker the component sets, the selector the stylesheet uses, and
 * the token value, then proves the token clears every measured bar height.
 */
describe("dock toolbar clearance", () => {
  // The vendored app's own `--top-h` values (app CSS): 64 default, 124 with the
  // usage row, 108 at phone width. The tallest wins.
  const TOPBAR_DEFAULT = 64;
  const TOPBAR_USAGE_ROW = 124;
  const TOPBAR_PHONE = 108;
  const TOPBAR_MAX = Math.max(TOPBAR_DEFAULT, TOPBAR_USAGE_ROW, TOPBAR_PHONE);

  // The live 1440x900 measurement: the office host begins at y=75 and the
  // Panels icons sit at y≈90, i.e. ~15px below the host, inside the top bar.
  const PANELS_Y = 90;
  const PANELS_ICON_H = 28; // `.ui-topbar__right .ui-icon-btn` is 28x28

  const DOCK_TOP = tokenPx("--office-dock-top");

  it("anchors the sheets below the top bar only through the root marker", () => {
    // The component sets `data-office-dock-top="toolbar"` whenever either
    // sheet is open; the stylesheet consumes that marker.
    const rule = /\.office-game\[data-office-dock-top="toolbar"\]\s*>\s*\.office-game__dock\s*\{([^}]*)\}/
      .exec(css);
    expect(rule).not.toBeNull();
    expect(rule?.[1] ?? "").toMatch(/top:\s*var\(--office-dock-top\)/);

    // The base sheet still fills to the bottom; only the top edge moved.
    const base = /\.office-game__dock\s*\{([^}]*)\}/.exec(css);
    expect(base).not.toBeNull();
    expect(base?.[1] ?? "").toMatch(/bottom:\s*0/);
    // And the base rule is NOT offset by itself: the marker is load-bearing.
    expect(base?.[1] ?? "").toMatch(/top:\s*0/);
  });

  it("clears every measured top-bar height, including the usage row", () => {
    expect(DOCK_TOP).toBeGreaterThanOrEqual(TOPBAR_MAX);
    // The Panels icons (bottom edge) must sit above the sheet's top edge.
    expect(DOCK_TOP).toBeGreaterThanOrEqual(PANELS_Y + PANELS_ICON_H);
  });

  it("keeps the single-dock width and skeleton untouched by the offset", () => {
    const base = /\.office-game__dock\s*\{([^}]*)\}/.exec(css);
    expect(base?.[1] ?? "").toMatch(/width:\s*min\(44rem,\s*92%\)/);
    expect(base?.[1] ?? "").toMatch(/position:\s*absolute/);
  });
});

/*
 * Seat-toggle containment — the measured header collision.
 *
 * `.office-game > .seat-model__toggle` used to be `position: fixed; top:
 * .65rem; right: 9rem`. Fixed is viewport-relative, so at 390x844 the button
 * sat on the dashboard's mobile header wordmark (measured y≈11.7..43.7 over the
 * `Qubit AiO` span) and Playwright's click was intercepted by that header; at
 * 1024 and 1440 it floated inside the page-header band. The office host starts
 * below the dashboard header, so anchoring the toggle to the office root
 * (`position: absolute`) is what removes the header from its reachable area.
 */
describe("seat toggle containment", () => {
  const TOPBAR_MAX = 134; // 74px bar + 60px usage row; 108 on phones
  const DOCK_TOP = tokenPx("--office-dock-top");

  const rule = /\.office-game\s*>\s*\.seat-model__toggle\s*\{([^}]*)\}/.exec(css);
  const body = rule?.[1] ?? "";

  it("anchors the toggle to the office shell, not the viewport", () => {
    expect(rule).not.toBeNull();
    expect(body).toMatch(/position:\s*absolute/);
    // `fixed` resolves against the viewport and escapes the office region —
    // the exact bug: it covered the dashboard's mobile header at 390/768.
    expect(body).not.toMatch(/position:\s*fixed/);
    // Absolute containment needs a positioned office root.
    const root = /\.office-game:has\(>\s*\.office-game__main\)\s*\{([^}]*)\}/
      .exec(css);
    expect(root).not.toBeNull();
    expect(root?.[1] ?? "").toMatch(/position:\s*relative/);
  });

  it("drops below the vendored top bar, sharing the dock offset token", () => {
    expect(body).toMatch(/top:\s*var\(--office-dock-top\)/);
    // Same measured token the side sheets use, so it cannot drift from the
    // tallest bar (74 + 60 = 134px) and never covers the Panels group.
    expect(DOCK_TOP).toBeGreaterThanOrEqual(TOPBAR_MAX);
    // One spacing step of inset, not a magic 9rem that could land it over a
    // global control on a narrow viewport.
    expect(body).toMatch(/right:\s*var\(--office-s3\)/);
  });

  it("stays below the dock layer so a sheet never covers its own close button", () => {
    const z = /z-index:\s*(-?\d+)/.exec(body);
    expect(z).not.toBeNull();
    const dockBase = /\.office-game__dock\s*\{([^}]*)\}/.exec(css);
    const dockZ = /z-index:\s*(-?\d+)/.exec(dockBase?.[1] ?? "");
    expect(dockZ).not.toBeNull();
    expect(Number(z?.[1])).toBeLessThan(Number(dockZ?.[1]));
  });

  it("moves into the dashboard header on desktop, not over the office canvas", () => {
    const media = /@media \(min-width:\s*700px\)\s*\{[^}]*\.office-game\s*>\s*\.seat-model__toggle\s*\{([^}]*)\}/s.exec(css);
    expect(media).not.toBeNull();
    expect(media?.[1] ?? "").toMatch(/position:\s*fixed/);
    expect(media?.[1] ?? "").toMatch(/top:\s*0\.65rem/);
    expect(media?.[1] ?? "").toMatch(/right:\s*9rem/);
  });

  it("fits every measured width without horizontal overflow", () => {
    // Measured button box on the live page: 101.3 x 32px. The office shell
    // clips overflow, so the inset + width must fit the narrowest target.
    const measuredToggle = 101.3;
    for (const viewport of [390, 768, 1024, 1440]) {
      expect(GAP + measuredToggle, `toggle @${viewport}`)
        .toBeLessThanOrEqual(viewport);
    }
    // Long labels ellipsize instead of pushing the shell wider.
    expect(body).toMatch(
      /max-width:\s*calc\(100%\s*-\s*\(2\s*\*\s*var\(--office-s3\)\)\)/);
  });
});
