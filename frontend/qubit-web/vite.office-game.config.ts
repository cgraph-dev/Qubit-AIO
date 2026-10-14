/* F04 — the office game's own Vite build.
 *
 * WHY A SEPARATE CONFIG
 * ---------------------
 * The dashboard plugin loader injects exactly ONE `<script>` per manifest entry
 * (`web/src/plugins/usePlugins.ts`: `script.src = assetRoot/entry`). The
 * current bundle is a hand-stitched concatenation of six IIFEs
 * (`scripts/build_office_bundle.py`) whose load ORDER is load-bearing — and
 * whose module set is about to grow a game engine, a typed adapter and UI
 * components. Concatenating a TypeScript engine by hand is not maintainable.
 *
 * So this config is the packaging spike F04 asks for: compile ONE entry that
 * registers the plugin, keep the host's React (never ship a second copy), and
 * record the cost. It is deliberately ADDITIVE — the existing
 * `build_office_bundle.py` and its `--check` gate keep working until this
 * produces a served artifact that passes the same plugin tests.
 *
 * The output lands beside the hand-built bundle under a DIFFERENT filename so
 * nothing that currently serves can break by this config existing.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const PLUGIN_DIR = path.resolve(
  __dirname, "../qubit-app/plugins/qubit-office");

export default defineConfig({
  plugins: [react()],

  // The plugin's source lives OUTSIDE this package's directory, so bare
  // specifiers like `phaser` and `react` do not resolve from where the files
  // actually are. Point them at this package's node_modules explicitly rather
  // than duplicating a dependency tree next to the plugin.
  resolve: {
    alias: {
      phaser: path.resolve(__dirname, "node_modules/phaser/dist/phaser.esm.js"),
    },
    dedupe: ["react", "react-dom"],
  },

  // The spike emits ONE file. Vite's default behaviour also copies `publicDir`
  // (the whole SPA's favicon, fonts and sibling plugins) into `outDir`, which
  // in a served directory is a silent overwrite of unrelated assets.
  publicDir: false,

  build: {
    // One entry, one file — the plugin loader has no multi-script support.
    lib: {
      entry: path.resolve(PLUGIN_DIR, "game/entry.ts"),
      name: "QubitOfficeGame",
      formats: ["iife"],
      fileName: () => "qubit-office.game.js",
    },
    // THE HOST OWNS REACT. A second React copy in the plugin would split the
    // hook runtime: the engine would mount into its own tree and context,
    // session and theme would not reach it, and the failure is silent.
    rollupOptions: {
      external: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime"],
      output: {
        globals: {
          react: "React",
          "react-dom": "ReactDOM",
          "react/jsx-runtime": "ReactJSXRuntime",
          "react/jsx-dev-runtime": "ReactJSXRuntime",
        },
        // Deterministic output so a rebuild with no source change is a no-op,
        // matching the guarantee `build_office_bundle.py --check` already makes.
        entryFileNames: "qubit-office.game.js",
        assetFileNames: "qubit-office.game.[ext]",
      },
    },
    // THE SERVED LOCATION. `_serve_qubit_plugin` (webui.py:1610-1620) serves
    // plugin files straight out of `frontend/qubit-app/plugins/`, which is a
    // COMMITTED directory — that is why `qubit-office.bundle.js` lives there.
    // `frontend/qubit-dist/` is gitignored and a clean clone must omit it
    // (tests/test_clean_clone_wave5.py), so the promoted entry cannot live
    // there or a fresh clone would serve a manifest pointing at nothing.
    outDir: PLUGIN_DIR,
    // This build owns exactly one filename; never empty a directory that holds
    // the hand-stitched bundle, the manifest and the renderer modules.
    emptyOutDir: false,
    // A game engine is large; the default 500 kB warning is not informative
    // for this artifact. The REAL size check is the recorded measurement in
    // docs/game-office/frontend-game/evidence/F04-PACKAGING.md.
    chunkSizeWarningLimit: 2000,
    sourcemap: false,
    minify: false, // the plugin dir is served for inspection, not for payload
  },
});
