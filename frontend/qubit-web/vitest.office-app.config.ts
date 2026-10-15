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
  test: {
    environment: "node",
    include: [path.join(APP_DIR, "**/*.test.ts")],
    testTimeout: 20000,
  },
});
