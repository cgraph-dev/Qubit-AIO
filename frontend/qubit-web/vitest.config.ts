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
