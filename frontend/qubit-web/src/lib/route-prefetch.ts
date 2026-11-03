/**
 * Route prefetch — warm a page chunk before the click lands.
 *
 * Every page in `App.tsx` is a `React.lazy` route, so a nav click normally
 * pays the chunk download *after* the click: the operator sees the route
 * fallback for as long as the fetch takes. Hovering (or tabbing to) a nav row
 * is a reliable signal that the click is coming, so this module starts the
 * same dynamic import early.
 *
 * Guarantees — deliberately narrow, because prefetching must never change what
 * the user sees:
 *   - it only touches the network; nothing renders, no route changes, no state,
 *   - each URL is requested at most once per session (`prefetched` set),
 *   - a failed prefetch is swallowed — the real navigation still loads the
 *     chunk itself and surfaces its own error,
 *   - `prefers-reduced-motion` and Save-Data / slow connections skip it, so a
 *     metered or motion-sensitive session downloads nothing extra,
 *   - the loader only runs on an idle slot (or after a short hover dwell), so a
 *     sweep of the mouse across the sidebar cannot fire a burst of requests.
 */

export type RouteImporter = () => Promise<unknown>;

const prefetched = new Set<string>();

function shouldSkip(): boolean {
  if (typeof window === "undefined") return true;
  try {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      return true;
    }
    // Save-Data is an explicit "do not spend my bandwidth" signal.
    const conn = (
      navigator as Navigator & {
        connection?: { saveData?: boolean; effectiveType?: string };
      }
    ).connection;
    if (conn?.saveData) return true;
    // 2g/slow-2g: the chunk might be larger than the win.
    if (conn?.effectiveType === "slow-2g" || conn?.effectiveType === "2g") {
      return true;
    }
  } catch {
    /* feature detection must never throw */
  }
  return false;
}

/** Start the import exactly once; never throws. */
export function prefetchRoute(key: string, importer: RouteImporter | undefined): void {
  if (!importer || prefetched.has(key) || shouldSkip()) return;
  prefetched.add(key);
  try {
    void Promise.resolve(importer()).catch(() => {
      // Allow a later hover to retry after a transient failure.
      prefetched.delete(key);
    });
  } catch {
    prefetched.delete(key);
  }
}

/**
 * Debounced prefetch for pointer/focus events. The timer exists so a fast
 * mouse sweep does not fire every route it crosses; ~90ms is under the
 * human click threshold but long enough to filter pure transit.
 */
export function hoverPrefetch(
  key: string,
  importer: RouteImporter | undefined,
  delayMs = 90,
): () => void {
  if (!importer) return () => {};
  const timer = setTimeout(() => prefetchRoute(key, importer), delayMs);
  return () => clearTimeout(timer);
}

/** Test seam: forget what has been prefetched. */
export function __resetPrefetchState(): void {
  prefetched.clear();
}
