import { useEffect, useLayoutEffect, useSyncExternalStore } from "react";
import { useLocation } from "react-router";
import {
  applyMangaAttribute,
  currentMangaEnabled,
  mangaRouteEligible,
  subscribeManga,
} from "./manga-store";
import "./manga.css";
import "./manga-v2.css";
import "./manga-v3.css";
import "./manga-v4.css";
import "./manga-v5.css";
import "./manga-rail-reference.css";
import "./manga-ux.css";
import "./manga-layout-repair.css";
import "./manga-title-and-inspector.css";
import "./manga-semantic-surfaces.css";
import "./manga-page-repair.css";
import "./manga-reference-components.css";
import "./manga-studio-pass.css";
import "./manga-reference-match.css";
import "./manga-overflow-guards.css";
import "./manga-neon-reference.css";
import "./manga-fidelity.css";
import "./manga-zip-sidebar-fix.css";
import "./manga-zip-surfaces.css";
import "./manga-zip-registry.css";
import "./manga-zip-routes.css";
import "./manga-sidebar-final.css";
import "./manga-zip-controls.css";
import "./manga-atom-titles.css";
import "./manga-final-fidelity.css";
import "./manga-precision-repair.css";

/** The skin only exists when the gate is mounted under the router. */
function useMangaPreference(): boolean {
  return useSyncExternalStore(
    subscribeManga,
    currentMangaEnabled,
    () => false,
  );
}

/**
 * Mount exactly once under the router. Paints nothing visible: it keeps the
 * <html> marker in sync with (Q6/Q6-L theme selection × eligible route).
 * The theme itself is selected in the Theme panel; there is no separate skin
 * switch.
 */
export function MangaSkinGate(): null {
  const { pathname } = useLocation();
  const enabled = useMangaPreference();

  // Layout effect: the marker must be gone before the excluded surface paints.
  useLayoutEffect(() => {
    applyMangaAttribute(enabled && mangaRouteEligible(pathname));
  }, [enabled, pathname]);

  // Enable the optional ZIP asset layer only when all original assets have
  // actually been installed. Missing files must never blank existing UI.
  useEffect(() => {
    const root = document.documentElement;
    const active = enabled && mangaRouteEligible(pathname);
    if (!active) {
      delete root.dataset.qubitMangaZip;
      return;
    }
    const controller = new AbortController();
    fetch("/manga-ui/zip/manifest.json", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Manga ZIP assets not installed");
        return response.json();
      })
      .then((manifest: unknown) => {
        if (!controller.signal.aborted && Array.isArray(manifest) && manifest.length === 89) {
          root.dataset.qubitMangaZip = "ready";
        } else if (!controller.signal.aborted) {
          delete root.dataset.qubitMangaZip;
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) delete root.dataset.qubitMangaZip;
      });
    return () => {
      controller.abort();
      delete root.dataset.qubitMangaZip;
    };
  }, [enabled, pathname]);

  useEffect(() => () => {
    applyMangaAttribute(false);
    delete document.documentElement.dataset.qubitMangaZip;
  }, []);

  return null;
}
