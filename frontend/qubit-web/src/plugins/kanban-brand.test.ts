/**
 * Kanban plugin brand guard.
 *
 * The kanban plugin's shipped `dashboard/dist` is hand-written first-party
 * code (not a vendored artifact we cannot rebuild), and it is served at
 * `/plugins/kanban/dist/*`. Its board/column/card classes are the
 * `qubit-kanban-*` namespace; the pre-rebrand `hermes-kanban-*` namespace must
 * be gone, and no legacy brand token may survive in the served JS/CSS.
 *
 * This scans the two first-party source copies directly:
 *   - `frontend/qubit-web/public/plugins/kanban/dist` (copied into the served
 *     `frontend/qubit-dist/plugins/kanban/dist` by the Vite build),
 *   - `qubit_runtime/plugins/kanban/dashboard/dist` (the plugin's own copy).
 *
 * Reading shipped bundles is the established pattern for brand scans in this
 * suite (see `plugin-globals.test.ts`); it is deliberately scoped to the
 * first-party plugin, never to third-party installed bundles whose dist we
 * do not own.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

/** The two first-party copies of the kanban plugin dashboard dist. */
const DIST_DIRS = [
  path.resolve(__dirname, "../../public/plugins/kanban/dist"),
  path.resolve(__dirname, "../../../../qubit_runtime/plugins/kanban/dashboard/dist"),
];

const FILES = ["index.js", "style.css"];

/** Legacy brand tokens retired from the product surface. */
const LEGACY_TOKENS = /hermes|openclaw|nousresearch/i;

describe("kanban plugin brand contract", () => {
  it.each(DIST_DIRS)("serves the qubit-kanban-* namespace from %s", (dir) => {
    const js = fs.readFileSync(path.join(dir, "index.js"), "utf8");
    const css = fs.readFileSync(path.join(dir, "style.css"), "utf8");
    expect(js, "kanban JS must use the qubit-kanban-* classes").toContain("qubit-kanban");
    expect(css, "kanban CSS must define the qubit-kanban-* classes").toContain("qubit-kanban");
    expect(js, "the retired hermes-kanban-* classes must be gone").not.toContain("hermes-kanban");
    expect(css, "the retired hermes-kanban-* classes must be gone").not.toContain("hermes-kanban");
  });

  it.each(DIST_DIRS)("carries no legacy brand token from %s", (dir) => {
    for (const file of FILES) {
      const text = fs.readFileSync(path.join(dir, file), "utf8");
      expect(text, `${path.join(dir, file)} still carries a legacy brand token`).not.toMatch(
        LEGACY_TOKENS,
      );
    }
  });
});
