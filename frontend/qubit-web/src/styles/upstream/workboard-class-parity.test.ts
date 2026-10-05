/**
 * Workboard class parity — every `workboard-*` / `btn` / `callout` /
 * `settings-*` class used by the two Qubit workboard pages must resolve to a
 * real selector in the shipped upstream-port CSS
 * (`src/styles/upstream/workboard.css`, imported from `src/index.css`).
 *
 * This is the regression net for the "pages emit workboard classes but zero
 * workboard CSS ships" failure: if someone adds markup referencing a class
 * the port does not define, this test fails.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_SRC = path.resolve(HERE, "../..");
const CSS_PATH = path.join(HERE, "workboard.css");
const PAGES = [
  path.join(REPO_SRC, "pages/TaskInboxPage.tsx"),
  path.join(REPO_SRC, "pages/BoardAdminPage.tsx"),
];

// Class tokens this suite owns: the upstream workboard surface plus the
// minimal upstream bases the port ships (btn / callout / settings-*).
const TARGET = /^(workboard-[A-Za-z0-9_-]*|btn[A-Za-z-]*|callout[A-Za-z-]*|settings-[A-Za-z-]*)$/;

function ownedTokens(source: string): Set<string> {
  const tokens = new Set<string>();
  // Every quoted literal in the page (className strings, conditional `"active"`
  // branches, `workboard-health__item--${key}` template prefixes, …).
  for (const match of source.matchAll(/["'`]([^"'`]*?)["'`]/g)) {
    for (const raw of match[1].split(/[\s${}()!?:,;]+/)) {
      const token = raw.replace(/^[.,]+|[.,]+$/g, "");
      if (TARGET.test(token)) tokens.add(token);
    }
  }
  return tokens;
}

function resolves(css: string, token: string): boolean {
  if (token.endsWith("-")) {
    // Dynamic suffix (`workboard-board--${layout}`): some selector must share
    // the static prefix.
    return css.includes(`.${token}`);
  }
  return new RegExp(`\\.${token}(?![A-Za-z0-9_-])`).test(css);
}

describe("workboard class parity", () => {
  it("ships the upstream port with provenance and zero legacy brand", () => {
    const css = fs.readFileSync(CSS_PATH, "utf-8");
    expect(css).toContain("7ff3b3be");
    expect(css).toContain("extensions/workboard/browser/styles/workboard.css");
    // Provenance comments carry the upstream repo URL and the legacy-brand
    // rename note by design (required provenance, never rendered); the actual
    // shipped selectors, vars, and companion copy must be brand-free.
    const body = css.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(body.toLowerCase()).not.toContain("openclaw");
    expect(body.toLowerCase()).not.toContain("hermes");
    expect(body.toLowerCase()).not.toContain("nous");
  });

  it("resolves every owned class used by TaskInboxPage + BoardAdminPage", () => {
    const css = fs.readFileSync(CSS_PATH, "utf-8");
    const missing: string[] = [];
    for (const page of PAGES) {
      const source = fs.readFileSync(page, "utf-8");
      for (const token of ownedTokens(source)) {
        if (!resolves(css, token)) missing.push(`${path.basename(page)}: .${token}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("covers the upstream DOM contract landmarks", () => {
    const css = fs.readFileSync(CSS_PATH, "utf-8");
    for (const landmark of [
      ".workboard",
      ".workboard-main",
      ".workboard-heading",
      ".workboard-toolbar",
      ".workboard-toolbar__filters",
      ".workboard-toolbar__tools",
      ".workboard-board-viewport",
      ".workboard-board--page",
      ".workboard-column__header",
      ".workboard-column__heading",
      ".workboard-column__count",
      ".workboard-column__cards",
      ".workboard-column__item",
      ".workboard-empty-state",
    ]) {
      expect(css, landmark).toContain(landmark);
    }
  });

  it("keeps the pages on the upstream DOM contract", () => {
    for (const page of PAGES) {
      const source = fs.readFileSync(page, "utf-8");
      for (const landmark of [
        'className="workboard"',
        "workboard-main",
        "workboard-heading",
        "workboard-toolbar",
        "workboard-board-viewport",
        "workboard-column",
        "workboard-empty-state",
      ]) {
        expect(source, `${path.basename(page)} ${landmark}`).toContain(landmark);
      }
    }
  });
});
