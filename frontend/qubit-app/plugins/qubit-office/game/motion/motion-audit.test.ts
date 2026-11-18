/* Stage 9 zero-control-motion audit for the Office motion layer.
 *
 * This test is the executable form of the Stage 9 rule: a theme or effects
 * sheet may animate atmosphere and panel containers, but it must never attach
 * `transition`/`animation` to an interactive control or to the immediate
 * wrapper of one.
 *
 * It fails on:
 *   - a control selector in a rule that declares transition/animation;
 *   - the removed Stage 8 feed-row flash (`.ui-feed__item` wraps a button),
 *     tab underline (`--office-tab-*`, `.ui-ttabs`), tooltip or toast motion;
 *   - a hex colour literal in `motion.css`;
 *   - a missing reduced-motion freeze for an animated selector;
 *   - a new frame loop (`requestAnimationFrame`/`setInterval`) in motion code.
 */

import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CSS_PATH = path.resolve(HERE, "../styles/motion.css");
const MOTION_DIR = HERE;

interface ParsedRule {
  readonly selector: string;
  readonly body: string;
  readonly reduced: boolean;
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Comments explain the rule; they are not behaviour, so drop them first. */
function stripSourceComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/**
 * Minimal CSS block walker. It understands `@media`/`@supports` nesting (which
 * is all the reduced-motion layer needs) and ignores keyframe bodies.
 */
function parseRules(css: string, reduced = false): ParsedRule[] {
  const rules: ParsedRule[] = [];
  let index = 0;
  while (index < css.length) {
    const open = css.indexOf("{", index);
    if (open === -1) {
      break;
    }
    const selector = css.slice(index, open).trim();
    let depth = 1;
    let cursor = open + 1;
    while (cursor < css.length && depth > 0) {
      const ch = css[cursor];
      if (ch === "{") {
        depth += 1;
      } else if (ch === "}") {
        depth -= 1;
      }
      cursor += 1;
    }
    const body = css.slice(open + 1, cursor - 1);

    if (/^@(media|supports)\b/.test(selector)) {
      const nowReduced = reduced
        || /prefers-reduced-motion|data-office-reduced-motion/.test(selector);
      rules.push(...parseRules(body, nowReduced));
    } else if (/^@(keyframes|-webkit-keyframes|font-face|layer)\b/.test(selector)) {
      // Keyframes are the animation definitions, not selectors we audit.
    } else if (!selector.startsWith("@")) {
      rules.push({
        selector,
        body,
        reduced: reduced
          || /data-office-reduced-motion="1"/.test(selector),
      });
    }
    index = cursor;
  }
  return rules;
}

function selectors(rule: ParsedRule): string[] {
  return rule.selector.split(",").map((part) => part.trim()).filter(Boolean);
}

/** Declares a motion property (transition/animation and their longhands). */
function declaresMotion(body: string): boolean {
  return /(^|[;\s])(transition|animation)(?!-play-state)(-[a-z-]+)?\s*:/i.test(body);
}

/**
 * Selector fragments that can address an interactive control, in the real
 * Habblaud and Qubit Office DOM, plus the control wrappers Stage 9 calls out.
 */
const CONTROL_FRAGMENTS = [
  "button",
  "input",
  "select",
  "textarea",
  ".ui-btn",
  ".office-game__btn",
  ".ui-icon-btn",
  ".office-theme-menu-btn",
  ".office-theme-dock-close",
  ".office-game__dock-close",
  ".tower__close",
  ".ui-ttab",
  ".warroom__tab",
  ".office-game__studioTab",
  ".office-game__campusTabs",
  ".ui-seg",
  ".ui-counter",
  ".ui-room__head",
  ".ui-switch",
  ".ui-perm__check",
  ".ui-agent",
  ".ui-feed__row",
  ".ui-feed__item",
  ".ui-toast",
  ".ui-tip",
  ".ui-search",
  ".ui-msg__input",
  ".ui-rename__input",
  ".room-builder__field",
  ".agents-panel__card",
  ".agents-panel__seat",
  '[role="tab"]',
  "[role='tab']",
  '[role="radio"]',
  "[role='radio']",
  '[role="switch"]',
  "[role='switch']",
  '[role="checkbox"]',
  "[role='checkbox']",
  '[role="button"]',
  "[role='button']",
  "sakuga-",
];

function isControlSelector(selector: string): boolean {
  const lowered = selector.toLowerCase();
  return CONTROL_FRAGMENTS.some((fragment) => lowered.includes(fragment));
}

const rawCss = stripComments(fs.readFileSync(CSS_PATH, "utf8"));
const rules = parseRules(rawCss);
const animatedRules = rules.filter((rule) => !rule.reduced && declaresMotion(rule.body));

describe("motion.css zero-control-motion audit", () => {
  it("never animates or transitions an interactive control", () => {
    const offenders: string[] = [];
    for (const rule of animatedRules) {
      for (const selector of selectors(rule)) {
        if (isControlSelector(selector)) {
          offenders.push(selector);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("omits the Stage 9 removal list entirely", () => {
    // The feed row's <li> wraps <button class="ui-feed__row">; a row flash is
    // control motion. The tab underline, tooltips and toasts are also gone.
    expect(rawCss).not.toMatch(/\.ui-feed__item\b/);
    expect(rawCss).not.toMatch(/\.ui-ttabs?\b/);
    expect(rawCss).not.toMatch(/--office-tab-[a-z]+\s*:/);
    expect(rawCss).not.toMatch(/\.ui-toast\b/);
    expect(rawCss).not.toMatch(/\.ui-tip\b/);
    expect(rawCss).not.toMatch(/data-fx-fresh/);
  });

  it("keeps the allowed ambient and panel motion", () => {
    const text = rawCss;
    expect(text).toContain('g[data-fx-node="moon"]');
    expect(text).toContain('g[data-fx-node="lantern"]');
    expect(text).toContain('g[data-fx-node="petal"]');
    expect(text).toContain(".ui-feed__ticker[data-fx-tick=\"1\"]");
    expect(text).toContain(".tower__roomName");
    expect(text).toContain(".office-game__dock");
    expect(text).toContain(".ui-dialog[open]");

    const animated = new Set(
      animatedRules.flatMap((rule) => selectors(rule)),
    );
    expect(animated.size).toBeGreaterThan(0);
  });

  it("contains no hex colour literals", () => {
    // Colours must come from tokens; geometry and durations are not colours.
    expect(rawCss).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });

  it("freezes every animated selector under prefers-reduced-motion", () => {
    const reducedMedia = rules
      .filter((rule) => rule.reduced)
      .flatMap((rule) => selectors(rule));
    const missing: string[] = [];
    for (const rule of animatedRules) {
      for (const selector of selectors(rule)) {
        if (!reducedMedia.includes(selector)) {
          missing.push(selector);
        }
      }
    }
    expect(missing, missing.join("\n")).toEqual([]);
  });

  it("freezes motion under the explicit shell attribute too", () => {
    const attributeRules = rules.filter(
      (rule) => /data-office-reduced-motion="1"/.test(rule.selector),
    );
    expect(attributeRules.length).toBeGreaterThan(0);

    const disablesAnimation = attributeRules.some(
      (rule) => /animation\s*:\s*none/.test(rule.body),
    );
    const disablesTransition = attributeRules.some(
      (rule) => /transition\s*:\s*none/.test(rule.body),
    );
    expect(disablesAnimation).toBe(true);
    expect(disablesTransition).toBe(true);
    expect(rawCss).toMatch(
      /data-office-reduced-motion="1"[\s\S]{0,500}?g\[data-fx-node="petal"\][\s\S]{0,500}?display:\s*none/,
    );
  });
});

describe("motion modules stay out of the frame loop", () => {
  const files = fs.readdirSync(MOTION_DIR)
    .filter((name) => /\.(ts|tsx)$/.test(name) && !name.endsWith(".test.ts"));

  it("has motion modules to check", () => {
    expect(files.length).toBeGreaterThanOrEqual(4);
  });

  it("never starts a requestAnimationFrame or interval loop", () => {
    for (const name of files) {
      const source = stripSourceComments(
        fs.readFileSync(path.join(MOTION_DIR, name), "utf8"));
      expect(source, name).not.toMatch(/requestAnimationFrame/);
      expect(source, name).not.toMatch(/setInterval/);
      expect(source, name).not.toMatch(/getContext\(\s*["']2d["']\s*\)/);
    }
  });

  it("does not mention the removed feed/toast/tab motion wiring", () => {
    for (const name of files) {
      const source = stripSourceComments(
        fs.readFileSync(path.join(MOTION_DIR, name), "utf8"));
      expect(source, name).not.toMatch(/attachTabUnderlineObserver/);
      expect(source, name).not.toMatch(/data-fx-fresh/);
    }
  });
});
