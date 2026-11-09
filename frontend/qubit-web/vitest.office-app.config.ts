/*
 * The office plugin's app-layer unit tests (`game/app/**`).
 *
 * WHY A SEPARATE CONFIG
 * ---------------------
 * `vitest.config.ts` scans `src/**` (the dashboard SPA) and
 * `vitest.shared.config.ts` scans the framework-neutral `@qubit/shared` layer.
 * Neither reaches `frontend/qubit-app/plugins/qubit-office/game/app/`, so the
 * plugin's own suites — including `api-bridge.test.ts`, which pins the auth
 * header the office iframe needs — were collected by NO config and therefore
 * ran NOWHERE. An unrun test is not a test. This config points the runner at
 * that directory, using this package's installed vitest.
 */
import { defineConfig } from "vitest/config";
import path from "node:path";

const HERE = path.dirname(new URL(import.meta.url).pathname);
const APP_DIR = path.resolve(
  HERE, "../qubit-app/plugins/qubit-office/game/app");

export default defineConfig({
  // The tests live OUTSIDE this package (../qubit-app/...). Under the default
  // node environment Vite serves them through /@fs; a per-file
  // `@vitest-environment jsdom` (office-menu.test.ts needs a DOM) otherwise
  // fails to load every out-of-root test with "Cannot find module /@fs/...".
  // Allow the repo root so the DOM environment can read them.
  server: {
    fs: { allow: [path.resolve(HERE, "../../..")] },
  },
  test: {
    environment: "node",
    include: [path.join(APP_DIR, "**/*.test.ts")],
    testTimeout: 20000,
  },
});
