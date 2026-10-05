import { defineConfig } from "vitest/config";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * Runs the framework-neutral `@qubit/shared/muse` unit tests. These have no
 * React/DOM dependency, so the environment is plain `node`. Kept separate from
 * `vitest.config.ts` (which scans `src/**` with the React compiler + jsdom)
 * because the shared layer must stay provably platform-free.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@qubit/shared": path.resolve(here, "../qubit-shared/src/index.ts"),
    },
  },
  test: {
    environment: "node",
    include: ["../qubit-shared/src/muse/**/*.test.ts"],
    testTimeout: 20000,
  },
});
