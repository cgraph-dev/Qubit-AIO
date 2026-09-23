/**
 * Static dashboard plugin fleet.
 *
 * Discovers plugin manifests that ship inside the served SPA
 * (`<base>/plugins/fleet.json`) so the pinned Achievements and Kanban tabs
 * register even while the compatibility backend advertises only the Qubit
 * overlay plugins. Assets load relative to each manifest's ``assetBase``;
 * the plugin bundles call the shared backend through the SDK fetch/ws
 * helpers, which apply the dashboard base path and auth.
 */
import { QUBIT_BASE_PATH } from "@/lib/api";
import type { PluginManifest } from "./types";

/** Dashboard-relative fleet manifest URL.
 *
 * Resolved lazily (not at module load) so importing this module never reads
 * `@/lib/api` at evaluation time: tests that partially mock `@/lib/api` (e.g.
 * SessionsPage.test.tsx) must not fail just because this overlay module is
 * transitively imported.
 */
export function fleetManifestPath(): string {
  return `${QUBIT_BASE_PATH}/plugins/fleet.json`;
}

export async function loadFleetManifests(): Promise<PluginManifest[]> {
  try {
    const res = await fetch(fleetManifestPath(), {
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
    if (!res.ok) return [];
    const data: unknown = await res.json();
    if (!Array.isArray(data)) return [];
    return data.filter(
      (m): m is PluginManifest =>
        !!m &&
        typeof m === "object" &&
        typeof (m as PluginManifest).name === "string",
    );
  } catch {
    return [];
  }
}

/** Resolve the dashboard-relative asset root for a plugin manifest. */
export function pluginAssetRoot(manifest: PluginManifest): string {
  return manifest.assetBase ?? `/dashboard-plugins/${manifest.name}`;
}
