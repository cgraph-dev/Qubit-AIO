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
import "./manga-component-contract.css";
import "./manga-studio-art.css";
import "./manga-approved-production.css";
import "./manga-production-layout-repair.css";

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

  // The approved production art is a separate, checksum-verified install.
  // Only activate it when every required asset was installed: no broken
  // backgrounds or silently mixed prototypes on first load.
  useEffect(() => {
    const root = document.documentElement;
    const active = enabled && mangaRouteEligible(pathname);
    delete root.dataset.qmApprovedAssets;
    if (!active) return;
    const controller = new AbortController();
    const required = [
      "01_sidebar_header.webp", "02_system_panel_frame.webp",
      "03_system_titlebar.webp", "04_tooltip_plate.webp",
      "05_section_titlebar_starless.webp", "06_primary_button.webp",
      "07_disabled_button.webp", "11_atom_emblem.webp",
      "12_anime_eye_panel.webp", "13_star_only.webp",
      "14_three_gold_bars.webp",
    ];
    fetch("/manga-ui/approved/MANIFEST.json", {
      signal: controller.signal, cache: "no-cache",
    }).then((response) => {
      if (!response.ok) throw new Error("Approved art pack not installed");
      return response.json();
    }).then((manifest: unknown) => {
      if (controller.signal.aborted) return;
      if (typeof manifest !== "object" || manifest === null ||
          !required.every((asset) => Object.hasOwn(manifest, asset))) {
        throw new Error("Approved art pack manifest incomplete");
      }
      // Manifest exists; verify core images decode before making this skin
      // visible. Other assets remain available on demand.
      return Promise.all([
        "02_system_panel_frame.webp",
        "05_section_titlebar_starless.webp",
        "13_star_only.webp",
      ].map((asset) => new Promise<void>((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve();
        image.onerror = () => reject(new Error(`Missing ${asset}`));
        image.src = `/manga-ui/approved/${asset}`;
      }))).then(() => {
        if (!controller.signal.aborted) root.dataset.qmApprovedAssets = "ready";
      });
    }).catch(() => {
      if (!controller.signal.aborted) delete root.dataset.qmApprovedAssets;
    });
    return () => {
      controller.abort();
      delete root.dataset.qmApprovedAssets;
    };
  }, [enabled, pathname]);

  // One page-visibility listener governs all slow CSS reflection loops.
  // Animation is also turned off for prefers-reduced-motion in CSS.
  useEffect(() => {
    const syncMotion = () => {
      document.documentElement.dataset.qmMotion =
        document.hidden ? "paused" : "running";
    };
    syncMotion();
    document.addEventListener("visibilitychange", syncMotion);
    return () => {
      document.removeEventListener("visibilitychange", syncMotion);
      delete document.documentElement.dataset.qmMotion;
    };
  }, []);

  useEffect(() => () => {
    applyMangaAttribute(false);
    delete document.documentElement.dataset.qubitMangaZip;
    delete document.documentElement.dataset.qmApprovedAssets;
  }, []);

  return null;
}
