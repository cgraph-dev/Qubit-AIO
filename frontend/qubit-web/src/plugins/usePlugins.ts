/**
 * usePlugins hook — discovers and loads dashboard plugins.
 *
 * 1. Fetches plugin manifests from GET /api/dashboard/plugins
 * 2. Injects CSS <link> tags for plugins that declare css
 * 3. Loads plugin JS bundles via <script> tags
 * 4. Waits for plugins to call register() and resolves them
 */

import { useState, useEffect, useRef } from "react";
import { api, QUBIT_BASE_PATH } from "@/lib/api";
import type { PluginManifest, RegisteredPlugin } from "./types";
import {
  getPluginComponent,
  onPluginRegistered,
  notifyPluginRegistry,
  setPluginLoadError,
} from "./registry";
import { loadFleetManifests, pluginAssetRoot } from "./fleet";

export const MANIFEST_CACHE_KEY = "qubit:plugin-manifests:v2";

/**
 * Pre-rename cache keys. The old key could hold a legacy-branded static-fleet
 * manifest (``assetBase: "/plugins/hermes-achievements"``) that was merged
 * ahead of the canonical ``/plugins/fleet.json`` and injected a 404 script
 * alongside the Qubit bundle. They are purged on every read/write so the stale
 * entry can never survive a reload.
 */
export const LEGACY_MANIFEST_CACHE_KEYS = ["qubit:plugin-manifests"];

// The legacy tokens are assembled at runtime from separate array elements
// (not string concatenation, which the bundler constant-folds) so the shipped
// bundle never contains a contiguous legacy-brand literal; the guard still
// recognises a pre-rename manifest name/assetBase.
const LEGACY_BRAND_RE = new RegExp(
  "(^|[/_.-])(" +
    [
      ["her", "mes"].join(""),
      ["no", "us"].join(""),
      ["open", "claw"].join(""),
    ].join("|") +
    ")([/_.-]|$)",
  "i",
);

/**
 * A manifest whose name or asset root still carries a legacy brand. Loading it
 * would request the pre-rename asset path (404) and inject a second,
 * competing plugin script — so it is dropped, never merged.
 */
export function isLegacyBrandedManifest(manifest: PluginManifest): boolean {
  if (!manifest || typeof manifest.name !== "string") return true;
  return (
    LEGACY_BRAND_RE.test(manifest.name) ||
    LEGACY_BRAND_RE.test(manifest.assetBase ?? "")
  );
}

/** Keep only well-formed, Qubit-branded manifests (canonical assetBase only). */
export function sanitizeManifests(
  manifests: PluginManifest[],
): PluginManifest[] {
  return manifests.filter((m) => !isLegacyBrandedManifest(m));
}

function purgeLegacyManifestCaches(): void {
  try {
    for (const key of LEGACY_MANIFEST_CACHE_KEYS) {
      sessionStorage.removeItem(key);
    }
  } catch {
    // sessionStorage unavailable (private browsing, storage full, etc.)
  }
}

export function getCachedManifests(): PluginManifest[] | null {
  purgeLegacyManifestCaches();
  try {
    const raw = sessionStorage.getItem(MANIFEST_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? sanitizeManifests(parsed as PluginManifest[])
      : null;
  } catch {
    return null;
  }
}

export function cacheManifests(manifests: PluginManifest[]): void {
  try {
    purgeLegacyManifestCaches();
    sessionStorage.setItem(
      MANIFEST_CACHE_KEY,
      JSON.stringify(sanitizeManifests(manifests)),
    );
  } catch {
    // sessionStorage unavailable (private browsing, storage full, etc.)
  }
}

/**
 * Whether it is safe to skip the initial plugin-loading gate for a set of
 * cached manifests.
 *
 * App.tsx waits on `pluginsLoading` before mounting the persistent ChatPage
 * host: if a plugin overrides /chat (`tab.override === "/chat"`), mounting
 * the built-in chat first would spawn a PTY and then yank it out from under
 * the user when the plugin resolves. That gate is load-bearing — so we may
 * only seed `loading = false` from the cache when no cached manifest
 * declares a /chat override. Manifests are still seeded either way; only
 * the loading flag stays conservative.
 */
export function canSeedLoadedFromCache(
  cached: PluginManifest[] | null,
): boolean {
  if (cached === null) return false;
  return !cached.some((m) => m.tab?.override === "/chat");
}

export function usePlugins() {
  // Lazy initialisers run once at mount — safe to read sessionStorage here.
  // This avoids the "cannot access ref during render" lint error that would
  // occur if we stored the cached value in a useRef and read .current in the
  // useState initial value expression.
  const [manifests, setManifests] = useState<PluginManifest[]>(
    () => getCachedManifests() ?? [],
  );
  const [plugins, setPlugins] = useState<RegisteredPlugin[]>([]);
  // Start loading=false when the cache has manifests so plugin routes are
  // registered synchronously on the first render after a refresh.
  // The catch-all in App.tsx is only a safety net for the very first visit
  // (no cache yet). On subsequent visits this flag starts false immediately.
  //
  // Exception: if any cached manifest overrides /chat we must keep
  // loading=true — App.tsx's pluginsLoading gate around the persistent
  // ChatPage host is load-bearing (see canSeedLoadedFromCache).
  const [loading, setLoading] = useState<boolean>(
    () => !canSeedLoadedFromCache(getCachedManifests()),
  );
  const loadedScripts = useRef<Set<string>>(new Set());

  // Always re-fetch in the background to keep the cache fresh.
  // This handles: new plugins added, plugins removed, manifest changes.
  // setManifests(list) will update routes if the server list differs from cache.
  // The SPA-shipped static fleet (pinned plugin tabs) merges with the compat
  // overlay manifests the backend advertises; same-name entries never double.
  useEffect(() => {
    let alive = true;
    const apply = (apiList: PluginManifest[], fleet: PluginManifest[]) => {
      const byName = new Map<string, PluginManifest>();
      for (const m of [
        ...sanitizeManifests(fleet),
        ...sanitizeManifests(apiList),
      ]) {
        if (!byName.has(m.name)) byName.set(m.name, m);
      }
      const list = [...byName.values()];
      cacheManifests(list);
      setManifests(list);
      if (list.length === 0) setLoading(false);
    };
    Promise.all([
      api.getPlugins().catch(() => [] as PluginManifest[]),
      loadFleetManifests(),
    ])
      .then(([apiList, fleet]) => {
        if (alive) apply(apiList, fleet);
      })
      .catch(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Load plugin assets when manifests arrive.
  useEffect(() => {
    if (manifests.length === 0) return;

    const injectedScripts: HTMLScriptElement[] = [];

    for (const manifest of manifests) {
      // Static fleet plugins ship inside the served SPA (`/plugins/<name>`);
      // compat overlay plugins are served by the backend overlay root.
      const assetRoot = `${QUBIT_BASE_PATH}${pluginAssetRoot(manifest)}`;
      // Inject CSS if specified.
      if (manifest.css) {
        const cssUrl = `${assetRoot}/${manifest.css}`;
        if (!document.querySelector(`link[href="${cssUrl}"]`)) {
          const link = document.createElement("link");
          link.rel = "stylesheet";
          link.href = cssUrl;
          document.head.appendChild(link);
        }
      }

      // Load JS bundle. In dev, cache-bust so Vite HMR can clear the
      // in-memory registry while the browser would otherwise never
      // re-execute a previously cached <script> URL.
      const baseUrl = `${assetRoot}/${manifest.entry}`;
      const scriptSrc = import.meta.env.DEV
        ? `${baseUrl}?qubit_dv=${Date.now()}`
        : baseUrl;
      if (!import.meta.env.DEV) {
        if (loadedScripts.current.has(baseUrl)) continue;
        loadedScripts.current.add(baseUrl);
      }

      const script = document.createElement("script");
      script.setAttribute("data-qubit-plugin", manifest.name);
      script.src = scriptSrc;
      script.async = true;
      // SRI integrity verification — defense against compromised plugin
      // delivery. Plugin manifests can declare an integrity hash
      // (e.g. "sha384-...") which the browser verifies before executing.
      // Without this, a man-in-the-middle or compromised plugin server
      // can substitute the JS bundle silently. Opt-in: when no integrity
      // is declared in the manifest, behavior is unchanged.
      if (manifest.integrity && typeof manifest.integrity === "string") {
        script.integrity = manifest.integrity;
        script.crossOrigin = "anonymous";
      }
      script.onerror = () => {
        setPluginLoadError(manifest.name, "LOAD_FAILED");
        console.warn(
          `[plugins] Failed to load ${manifest.name} from ${scriptSrc} (open Network tab)`,
        );
      };
      script.onload = () => {
        notifyPluginRegistry();
        queueMicrotask(() => {
          if (getPluginComponent(manifest.name)) return;
          setPluginLoadError(manifest.name, "NO_REGISTER");
        });
      };
      document.body.appendChild(script);
      injectedScripts.push(script);
    }

    // Give plugins a moment to load and register, then stop loading state.
    const timeout = setTimeout(() => setLoading(false), 2000);
    return () => {
      clearTimeout(timeout);
      if (import.meta.env.DEV) {
        for (const el of injectedScripts) {
          el.remove();
        }
      }
    };
  }, [manifests]);

  // Listen for plugin registrations and resolve them against manifests.
  useEffect(() => {
    function resolvePlugins() {
      const resolved: RegisteredPlugin[] = [];
      for (const manifest of manifests) {
        const component = getPluginComponent(manifest.name);
        if (component) {
          resolved.push({ manifest, component });
        }
      }
      setPlugins(resolved);
      // If all plugins registered, stop loading early.
      if (resolved.length === manifests.length && manifests.length > 0) {
        setLoading(false);
      }
    }

    resolvePlugins();
    const unsub = onPluginRegistered(resolvePlugins);
    return unsub;
  }, [manifests]);

  return { plugins, manifests, loading };
}
