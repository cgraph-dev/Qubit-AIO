/**
 * Theme-token contract guard.
 *
 * WHY THIS EXISTS
 * Several console surfaces rendered invisible content — blank pills, unreadable
 * labels, panels with no outline — and the cause was NOT missing CSS. It was
 * components reading CSS custom properties that either do not exist at `:root`
 * or resolve to a TRANSPARENT value:
 *
 *   --foreground       -> color-mix(in srgb, #ffffff 0%, transparent)  (transparent!)
 *   --border           -> not defined at :root
 *   --primary          -> not defined at :root
 *   --destructive      -> not defined at :root
 *   --muted-foreground -> not defined at :root
 *
 * The working set is the `@theme` remap to `--color-*` in `src/index.css`.
 * A component that reads the raw name gets nothing, and nothing warns you: a
 * custom property that is simply undefined makes the whole declaration
 * invalid-at-computed-value time, so the element falls back to inherited
 * colour or no border at all.
 *
 * These tests pin the two facts that make the failure possible, so a future
 * change cannot quietly reintroduce it:
 *   1. the raw names that must NEVER be used as a value must stay absent (or
 *      stay transparent), and
 *   2. every `var(--x)` written in our own stylesheets must either reference a
 *      token that exists, or carry a fallback that does.
 *
 * They read real source text rather than rendering, so they are fast and fail
 * at the exact offending line.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "..");
const INDEX_CSS = join(SRC, "index.css");

/**
 * Raw property names that must never be *consumed* directly for text or
 * borders. Either absent at `:root` or resolving transparent. The `--color-*`
 * remap is the supported surface. Documented here because this class of
 * mistake is invisible at runtime — the declaration simply drops.
 *
 * (Not asserted against, because `--foreground` legitimately exists as the
 * transparent base; the guard is the pattern test below.)
 */

/**
 * Token names that resolve at runtime.
 *
 * Two sources:
 *  1. `:root` declarations — `index.css` plus `console-tokens.css`, which
 *     hoists the ported upstream set globally.
 *  2. Declarations inside a component-scoped block (`.workboard { --x: … }`,
 *     `.hx-root { --v2-*: … }`). Those DO resolve for elements inside that
 *     scope, so a reference to them is not a defect; flagging them would bury
 *     the real breakage in false positives.
 *
 * The genuine failure is a reference whose token is declared in NEITHER place
 * and carries no fallback: the declaration is invalid-at-computed-value time
 * and silently drops.
 */
function readIndexCss(): string {
  return readFileSync(INDEX_CSS, "utf8");
}

function resolvableTokens(): Set<string> {
  const out = new Set<string>();
  for (const m of readIndexCss().matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)) {
    out.add(m[1]);
  }
  for (const file of ourStyleSheets()) {
    const code = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of code.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)) out.add(m[1]);
  }
  return out;
}

/** Every `.css` file we ship, excluding third-party imports. */
function ourStyleSheets(): string[] {
  const dir = join(SRC, "styles");
  const found: string[] = [];
  const walk = (d: string) => {
    for (const name of readdirSync(d)) {
      const full = join(d, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (name.endsWith(".css")) found.push(full);
    }
  };
  walk(dir);
  return found;
}

describe("theme token contract", () => {
  it("documents the transparent/absent raw vars the console must not use", () => {
    const css = readIndexCss();
    // `--foreground` is deliberately declared at alpha 0 (the base/alpha trio
    // ThemeProvider rewrites). If someone makes it opaque, this guard's premise
    // changed and the FORBIDDEN list should be re-reviewed.
    expect(css).toMatch(
      /--foreground:\s*color-mix\(in srgb,\s*#ffffff\s*0%,\s*transparent\)/,
    );
    expect(css).toMatch(/--foreground-base:\s*#ffffff/);
    expect(css).toMatch(/--foreground-alpha:\s*0/);
  });

  it("keeps the supported --color-* remap in place", () => {
    const css = readIndexCss();
    // These are the names components are allowed to consume. If one is removed
    // the surfaces that use it go invisible again.
    for (const token of [
      "--color-foreground",
      "--color-border",
      "--color-card",
      "--color-muted",
    ]) {
      expect(css).toContain(`${token}:`);
    }
  });

  it("hoists the ported token set so pages that never opt in still resolve", () => {
    // The root cause of the invisible/unstyled pages: the token set was scoped
    // to `.qb-upstream`, which only /open-viking carries, so every other page
    // had --text/--muted/--border/--space-* UNSET and the rules reading them
    // dropped. `console-tokens.css` hoists the same values onto `:root`.
    const hoisted = readFileSync(
      join(SRC, "styles/upstream/console-tokens.css"),
      "utf8",
    );
    for (const token of [
      "--text",
      "--muted",
      "--border",
      "--space-2",
      "--bg-hover",
      "--radius-md",
      "--control-ui-text-sm",
    ]) {
      expect(
        hoisted,
        `${token} must be hoisted to :root or pages without .qb-upstream lose it`,
      ).toContain(`${token}:`);
    }
    // It must be a real :root block, not another scoped wrapper.
    expect(hoisted).toMatch(/:root\s*\{/);
    expect(hoisted).not.toMatch(/\.qb-upstream\s*\{/);
  });

  it("keeps the ported token values in the scoped block too", () => {
    // Hoisting must not be done by MOVING the definitions: a page inside
    // `.qb-upstream` still needs them, and the accessible `--muted`-as-text
    // decision documented there must survive.
    const compat = readFileSync(
      join(SRC, "styles/upstream/upstream-compat.css"),
      "utf8",
    );
    expect(compat).toMatch(/\.qb-upstream\s*\{/);
    expect(compat).toContain("--muted:");
    expect(compat).toContain("--muted-surface:");
    expect(compat).toContain("--text:");
    expect(compat).toContain("--space-2:");
  });

  it("every var() in our stylesheets is resolvable or carries a fallback", () => {
    const declared = resolvableTokens();
    const offenders: string[] = [];

    for (const file of ourStyleSheets()) {
      const text = readFileSync(file, "utf8");
      // Strip comments so prose mentioning var() does not count.
      const code = text.replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of code.matchAll(/var\(\s*(--[a-z0-9-]+)\s*(,)?/g)) {
        const [, name, hasFallback] = m;
        if (declared.has(name)) continue; // resolves
        if (hasFallback) continue; // invalid-at-computed-value time is avoided
        offenders.push(`${file.replace(SRC + "/", "")}: var(${name})`);
      }
    }

    expect(
      offenders,
      `These var() references have no defined token and no fallback, so the ` +
        `whole declaration is dropped:\n${offenders.join("\n")}`,
    ).toEqual([]);
  });
});
