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
