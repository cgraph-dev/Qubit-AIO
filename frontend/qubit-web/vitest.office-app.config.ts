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
const LOADING_DIR = path.resolve(
  HERE, "../qubit-app/plugins/qubit-office/game/loading");
const ART_DIR = path.resolve(
  HERE, "../qubit-app/plugins/qubit-office/game/art");
const MOTION_DIR = path.resolve(
  HERE, "../qubit-app/plugins/qubit-office/game/motion");

export default defineConfig({
  // The tests live OUTSIDE this package (../qubit-app/...). Under the default
  // node environment Vite serves them through /@fs; a per-file
  // `@vitest-environment jsdom` (office-menu.test.ts needs a DOM) otherwise
  // fails to load every out-of-root test with "Cannot find module /@fs/...".
  // Allow the repo root so the DOM environment can read them.
  server: {
    fs: { allow: [path.resolve(HERE, "../../..")] },
  },
  // The tests live outside this package, so Vite's bare-specifier resolution
  // would walk up from the test file and miss this package's node_modules.
  // Point React at the package's own copy (one React, never a second runtime).
  resolve: {
    alias: {
      react: path.resolve(HERE, "node_modules/react"),
      "react-dom": path.resolve(HERE, "node_modules/react-dom"),
    },
  },
  test: {
    environment: "node",
    // `app/**` is the office shell; `loading/**` is the Phase-1 loading screen
    // and its weather/pixelfx subset. Both live outside this package.
    include: [
      path.join(APP_DIR, "**/*.test.ts"),
      path.join(LOADING_DIR, "**/*.test.ts"),
      // Stage 5/6 art contracts and Stage 8 motion contracts.
      path.join(ART_DIR, "**/*.test.ts"),
      path.join(MOTION_DIR, "**/*.test.ts"),
    ],
    testTimeout: 20000,
  },
});
