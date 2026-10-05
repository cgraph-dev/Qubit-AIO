// Qubit harness: React's production bundle strips `act` entirely
// (`exports.act` only exists in react.development.js; react/index.js picks
// the bundle by `process.env.NODE_ENV`). Vitest defaults an *unset*
// NODE_ENV to "test", but an ambient `NODE_ENV=production` leaks through
// and every `import { act } from "react"` resolves to `undefined`
// ("act is not a function"). Force the test default back: the config loads
// before workers fork, so they inherit the fixed value before React loads.
if (process.env.NODE_ENV === "production") {
  process.env.NODE_ENV = "test";
}

import { defineConfig } from "vitest/config";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";

/** Same component/hook-scoped compiler preset as vite.config.ts. */
function compilerPreset() {
  const preset = reactCompilerPreset();
  preset.rolldown.filter = { ...preset.rolldown.filter, code: /\/>|<\/|from\s*['"][^'"]*react/ };
  return preset;
}
import path from "path";

export default defineConfig({
  plugins: [react(), babel({ presets: [compilerPreset()] })],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // jsdom + the React compiler make each file expensive to transform; when
    // the whole suite runs in parallel a single file can exceed Vitest's 5s
    // default and fail on timing alone. Give the files room to finish.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
});
