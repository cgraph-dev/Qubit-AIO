/**
 * Harness-console class parity (static half) — every `hx-*` / `ak-*` class
 * HarnessRouterPage uses resolves in the shipped
 * `src/styles/upstream/harness.css`, which keeps its provenance header and
 * carries no legacy product marks outside it.
 *
 * Node env (not jsdom): `node:fs` does not load under jsdom in this repo's
 * vitest setup. The render half lives in
 * `HarnessRouterPage.classParity.test.tsx`.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const PAGE = path.resolve(__dirname, "HarnessRouterPage.tsx");
const CSS = path.resolve(__dirname, "../styles/upstream/harness.css");
const INDEX_CSS = path.resolve(__dirname, "../index.css");

function consoleClassesInSource(file: string): Set<string> {
  const src = fs.readFileSync(file, "utf8");
  const found = new Set<string>();
  for (const m of src.matchAll(/\b(?:hx|ak)-[a-z0-9-]+/g)) found.add(m[0]);
  return found;
}

describe("harness stylesheet covers the page", () => {
  it("defines every hx-*/ak-* class the page uses", () => {
    const css = fs.readFileSync(CSS, "utf8");
    const used = consoleClassesInSource(PAGE);
    expect(used.size).toBeGreaterThan(30);
    const missing = [...used].filter(
      (cls) => !new RegExp(`\\.${cls}(?![A-Za-z0-9_-])`).test(css),
    );
    expect(missing).toEqual([]);
  });

  it("keeps provenance and ships through index.css", () => {
    const css = fs.readFileSync(CSS, "utf8");
    expect(css).toContain("harnessrouter");
    expect(css).toContain("f3192445c82da533fe83b125917efb8c3947aef0");
    expect(css).toContain("Apache-2.0");
    expect(css).toContain("ui/src/app/v2.css");
    expect(css).toContain("ui/src/app/revamp.css");
    // Legacy product handle appears nowhere in the shipped rules. The
    // provenance header above the closing `*/` is the one required legal
    // exception — it must name the real upstream repo, licence and source
    // paths (asserted directly above), so only the rule BODY is checked here.
    const body = css.slice(css.indexOf("*/") + 2);
    expect(body).not.toMatch(/harnessrouter/i);
    expect(fs.readFileSync(INDEX_CSS, "utf8")).toContain("./styles/upstream/harness.css");
  });
});
