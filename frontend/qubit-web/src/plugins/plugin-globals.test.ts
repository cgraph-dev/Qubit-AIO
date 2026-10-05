/**
 * Plugin globals rebrand guard.
 *
 * Two separate facts, two assertions:
 *
 * 1. FIRST-PARTY CODE uses the rebranded globals. The host dashboard exposes
 *    the plugin contract as `window.__QUBIT_PLUGIN_SDK__` / `__QUBIT_PLUGINS__`
 *    (see `src/plugins/registry.ts`), and only that surface is ours to choose.
 *
 * 2. INSTALLED BUNDLES may still read the pre-rebrand names, and the host must
 *    therefore ALSO expose them. A plugin's shipped `dist` is a build artifact
 *    we do not own and cannot rebuild; `agent-analytics` does
 *    `const SDK = window.__HERMES_PLUGIN_SDK__` and immediately destructures
 *    `React` from it, so when the alias is missing the plugin throws
 *    "Cannot destructure property 'React' of 'SDK' as it is undefined" and
 *    takes the whole page down with it.
 *
 * An earlier version of this suite asserted NO bundle referenced the retired
 * names. That was the wrong invariant: it demanded we fix artifacts we cannot
 * rebuild, and it passed while the real bug (a missing alias in the host) made
 * every route throw. The host shipping the alias is the fix; the bundle's
 * contents are out of our control. Accept-both, canonical first — the same
 * pattern used for `requires_hermes`/`requires_qubit`.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

/** Globals the rebrand retired, mapped to what the host exposes now. */
const RETIRED = ["__HERMES_PLUGIN_SDK__", "__HERMES_PLUGINS__", "__HERMES_SESSION_TOKEN__"];

/** The rebranded names first-party code must use. */
const CANONICAL = ["__QUBIT_PLUGIN_SDK__", "__QUBIT_PLUGINS__"];

const REGISTRY = path.resolve(__dirname, "registry.ts");

/**
 * Strip comments so attribution prose naming the upstream project cannot
 * masquerade as a live reference. Block + line comments, in that order.
 */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/** Every plugin bundle directory the dashboard serves assets from. */
function bundleRoots(): string[] {
  const home = process.env.HOME ?? "";
  const roots = [
    path.join(home, ".local/state/qubit-voicebridge/runtime/plugins"),
    path.resolve(__dirname, "../../../../qubit_runtime/plugins"),
  ];
  return roots.filter((r) => fs.existsSync(r));
}

/** Recursively find plugin entry bundles (`dist/index.js`). */
function findBundles(dir: string, out: string[] = []): string[] {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".git") continue;
      findBundles(full, out);
    } else if (entry.name === "index.js" && path.basename(path.dirname(full)) === "dist") {
      out.push(full);
    }
  }
  return out;
}

describe("plugin globals rebrand contract", () => {
  it("first-party code uses the rebranded globals", () => {
    const code = codeOnly(fs.readFileSync(REGISTRY, "utf8"));
    // The host must define the canonical names…
    for (const g of CANONICAL) {
      expect(code, `registry must expose ${g}`).toContain(`window.${g} =`);
    }
  });

  it("the host also exposes the retired names so old bundles still load", () => {
    // This is the actual fix. Without these assignments an installed bundle
    // built before the rebrand loads `undefined` as its SDK and throws.
    const code = codeOnly(fs.readFileSync(REGISTRY, "utf8"));
    for (const g of RETIRED.slice(0, 2)) {
      expect(
        code,
        `registry must expose the historical alias ${g} — an installed bundle ` +
          "reads it and throws 'Cannot destructure property React of SDK' " +
          "when it is undefined",
      ).toContain(`window.${g} =`);
    }
  });

  it("survives a real bundle that reads the retired globals", () => {
    // Passes vacuously on a machine with no installed plugins (documented, not
    // hidden): the assertion above is the load-bearing one. When a bundle IS
    // present, record which retired names it reads so a future rename knows
    // exactly which aliases must survive.
    const bundles = bundleRoots().flatMap((r) => findBundles(r));
    const readers: Record<string, string[]> = {};
    for (const bundle of bundles) {
      const code = codeOnly(fs.readFileSync(bundle, "utf8"));
      const hits = RETIRED.filter((g) => code.includes(g));
      if (hits.length) readers[bundle] = hits;
    }
    // Whatever a bundle reads, the host must expose it — assert that pairing
    // rather than asserting the bundle is clean.
    const registryCode = codeOnly(fs.readFileSync(REGISTRY, "utf8"));
    for (const [bundle, globals] of Object.entries(readers)) {
      for (const g of globals) {
        expect(
          registryCode,
          `${bundle} reads ${g} but the host no longer exposes it`,
        ).toContain(`window.${g} =`);
      }
    }
  });
});
