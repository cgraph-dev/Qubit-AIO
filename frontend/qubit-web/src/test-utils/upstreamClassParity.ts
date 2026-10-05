import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Load shipped upstream CSS files from disk (the same files index.css
 * imports into the bundle). Reads via node:fs rather than `?raw` imports,
 * which resolve to an empty module under this repo's vitest transform.
 *
 * Every page in the parity family emits upstream-namespaced classes
 * (`session-*`, `sessions-*`, `cron-*`, `device-*`, `settings-*`, `btn`,
 * `callout`) while shipping the ported upstream CSS from
 * `src/styles/upstream/`. This helper scans a rendered root for every class
 * token matching the page's upstream prefixes and asserts each one resolves
 * to a real selector in the shipped CSS text (imported `?raw`).
 *
 * Qubit-invented companions (`session-picker-*`, `worktree-*`,
 * `checkpoint-*`, …) are outside the upstream namespaces and are styled by
 * the Tailwind utilities on the same elements, so they are out of scope here.
 */

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function collectUpstreamClasses(root: ParentNode, prefixes: string[]): string[] {
  const found = new Set<string>();
  for (const el of root.querySelectorAll("[class]")) {
    const cls = el.getAttribute("class") ?? "";
    for (const token of cls.split(/\s+/)) {
      if (!token) continue;
      if (prefixes.some((p) => (p === "btn" || p === "callout" ? token === p || token.startsWith(`${p}-`) || token.startsWith(`${p}.`) : token.startsWith(p)))) {
        found.add(token.split(":")[0]);
      }
    }
  }
  // Also scan the root element itself (querySelectorAll excludes it).
  const rootEl = root instanceof Element ? root : root instanceof Document ? root.documentElement : null;
  if (rootEl) {
    for (const token of (rootEl.getAttribute("class") ?? "").split(/\s+/)) {
      if (prefixes.some((p) => (p === "btn" || p === "callout" ? token === p || token.startsWith(`${p}-`) : token.startsWith(p)))) {
        found.add(token.split(":")[0]);
      }
    }
  }
  return [...found].sort();
}

export function loadUpstreamCss(...names: string[]): string[] {
  // process.cwd() under vitest is the web root (frontend/qubit-web).
  return names.map((name) =>
    readFileSync(resolve(process.cwd(), "src/styles/upstream", name), "utf8"),
  );
}

export function assertUpstreamClassesResolve(
  root: ParentNode,
  cssTexts: string[],
  prefixes: string[],
): string[] {
  const css = cssTexts.join("\n");
  const classes = collectUpstreamClasses(root, prefixes);
  const missing = classes.filter(
    (cls) => !new RegExp(`(^|[,{\\s])\\.${escapeRegExp(cls)}(?![\\w-])`).test(css),
  );
  if (missing.length > 0) {
    throw new Error(
      `upstream class-parity failure: ${missing.length} class(es) used but not defined in shipped CSS: ${missing.join(", ")}`,
    );
  }
  return classes;
}
