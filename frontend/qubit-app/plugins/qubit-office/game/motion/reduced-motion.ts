/* Reduced-motion detection for the Office effects layer.
 *
 * Two independent switches, both honoured:
 *   1. the OS query `(prefers-reduced-motion: reduce)`;
 *   2. the player's explicit shell attribute
 *      `[data-office-reduced-motion="1"]` on `.office-game`, `.ui-root` or
 *      `<html>`.
 *
 * The CSS in `styles/motion.css` already freezes every animation under both
 * switches. This module exists so the DOM controller (the activity ticker)
 * also stops emitting pulse attributes instead of relying on a 1ms animation
 * that still mutates state.
 */

function safeDocument(): Document | null {
  return typeof document !== "undefined" ? document : null;
}

function hasReducedAttribute(node: Element | null): boolean {
  return Boolean(node && node.getAttribute("data-office-reduced-motion") === "1");
}

/**
 * True when either the OS or the shell asks for reduced motion. Safe in a
 * non-DOM environment (returns `false`).
 */
export function isReducedMotionActive(
  hostRoot?: ParentNode | Element | null,
): boolean {
  if (typeof window !== "undefined"
      && typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return true;
  }

  const doc = safeDocument();
  if (!doc) {
    return false;
  }
  if (hasReducedAttribute(doc.documentElement)) {
    return true;
  }
  if (hostRoot && "closest" in hostRoot) {
    return hasReducedAttribute(
      hostRoot as Element,
    ) || Boolean((hostRoot as Element).closest('[data-office-reduced-motion="1"]'));
  }
  return Boolean(
    doc.querySelector('.office-game[data-office-reduced-motion="1"],'
      + ' .ui-root[data-office-reduced-motion="1"]'),
  );
}

/**
 * Subscribe to both switches. Returns a cleanup function. The caller receives
 * a synchronous first call so it never starts in the wrong state.
 *
 * Observers are optional: a host without `MutationObserver` still gets the
 * media-query listener.
 */
export function subscribeReducedMotion(
  onChange: (reduced: boolean) => void,
  hostRoot?: HTMLElement | null,
): () => void {
  if (typeof window === "undefined") {
    return () => {};
  }

  const sync = () => onChange(isReducedMotionActive(hostRoot));
  sync();

  const mql = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
  mql?.addEventListener("change", sync);

  let observer: MutationObserver | null = null;
  const doc = safeDocument();
  if (doc && typeof MutationObserver !== "undefined") {
    observer = new MutationObserver(sync);
    observer.observe(doc.documentElement, {
      attributes: true,
      attributeFilter: ["data-office-reduced-motion"],
    });
    const scope = hostRoot ?? doc.querySelector<HTMLElement>(".office-game");
    if (scope && scope !== doc.documentElement) {
      observer.observe(scope, {
        attributes: true,
        attributeFilter: ["data-office-reduced-motion"],
      });
    }
  }

  return () => {
    mql?.removeEventListener("change", sync);
    observer?.disconnect();
  };
}
