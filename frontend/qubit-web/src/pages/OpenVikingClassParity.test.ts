/**
 * Open Viking class parity: every class the five pages and
 * `viking-settings.tsx` emit must resolve in a shipped stylesheet. An
 * undefined class renders as unstyled DOM, so this is a real gate — the
 * test reads the actual CSS files on disk, not a hand-maintained list.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const STYLES = join(SRC, "styles");

function readAllCss(): string {
  const parts: string[] = [readFileSync(join(SRC, "index.css"), "utf-8")];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (entry.name.endsWith(".css")) parts.push(readFileSync(p, "utf-8"));
    }
  };
  walk(STYLES);
  return parts.join("\n");
}

const CSS = readAllCss();
const defined = new Set(
  [...CSS.matchAll(/\.([a-zA-Z][a-zA-Z0-9_-]*)(?=[\s,{:>~+.)[])/g)].map((m) => m[1]),
);

/** Classes the Open Viking surface relies on (settings contract + shell). */
const VIKING_CLASSES = [
  "settings-page",
  "settings-page--wide",
  "settings-page__intro",
  "settings-section",
  "settings-section__header",
  "settings-section__copy",
  "settings-section__heading",
  "settings-count",
  "settings-section__desc",
  "settings-section__actions",
  "settings-group",
  "settings-row",
  "settings-row--nav",
  "settings-row--stacked",
  "settings-row--actions",
  "settings-row__text",
  "settings-row__title",
  "settings-row__desc",
  "settings-row__control",
  "settings-row__chevron",
  "settings-row__value",
  "settings-row__value--mono",
  "settings-empty",
  "settings-loading-skeleton",
  "settings-loading-skeleton__rows",
  "settings-loading-skeleton__row",
  "settings-loading-skeleton__title",
  "settings-loading-skeleton__description",
  "settings-loading-skeleton__control",
  "settings-status",
  "settings-status__dot",
  "settings-input",
  "viking-settings",
  "viking-intro",
  "viking-breadcrumb",
  "viking-breadcrumb__link",
  "viking-breadcrumb__sep",
  "viking-subnav",
  "viking-subnav__item",
];

describe("Open Viking class parity", () => {
  it("every emitted viking class is defined in a shipped sheet", () => {
    const missing = VIKING_CLASSES.filter((c) => !defined.has(c));
    expect(missing).toEqual([]);
  });

  it("the viking shell sheet is imported by index.css", () => {
    const index = readFileSync(join(SRC, "index.css"), "utf-8");
    expect(index).toContain("styles/upstream/viking-settings.css");
  });
});
