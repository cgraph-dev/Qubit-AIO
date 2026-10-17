/**
 * Recovery for stale-chunk navigation failures.
 *
 * Route chunks are content-hashed and immutable; when a rebuild (or a manual
 * cleanup) removes the chunk an already-open tab's index references, the
 * route's dynamic import rejects with "Failed to fetch dynamically imported
 * module" and the page stays broken until a manual refresh. Vite fires
 * `vite:preloadError` on the window in exactly this case, so listen: the
 * first preload error per page load reloads to the current index (which
 * references live chunks).
 *
 * Loop guard: the reload sets a sessionStorage flag, consumed at install. A
 * page that itself loaded from such a reload does NOT reload again — if the
 * chunk is genuinely missing (offline, half-written dist) the error surfaces
 * instead of bouncing forever. A later successful navigation clears nothing;
 * each fresh page load re-arms exactly one recovery.
 */
const RELOAD_FLAG = "qubit.chunk-reload-retry";

function readStore(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null,
): Pick<Storage, "getItem" | "setItem" | "removeItem"> | null {
  if (storage) return storage;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function installChunkReloadRecovery(
  target: Pick<Window, "addEventListener" | "removeEventListener"> = window,
  reload: () => void = () => window.location.reload(),
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = null,
): () => void {
  const store = readStore(storage);
  // Consume a pending retry marker left by the previous page load: this
  // load IS the recovery, so a further preload error must surface rather
  // than bounce.
  let alreadyRetried = false;
  try {
    alreadyRetried = store?.getItem(RELOAD_FLAG) === "1";
    if (alreadyRetried) store?.removeItem(RELOAD_FLAG);
  } catch {
    alreadyRetried = false;
  }
  const onPreloadError = () => {
    if (alreadyRetried) return;
    alreadyRetried = true;
    try {
      store?.setItem(RELOAD_FLAG, "1");
    } catch {
      /* without storage the reload still recovers the common case */
    }
    reload();
  };
  target.addEventListener("vite:preloadError", onPreloadError);
  return () =>
    target.removeEventListener("vite:preloadError", onPreloadError);
}
