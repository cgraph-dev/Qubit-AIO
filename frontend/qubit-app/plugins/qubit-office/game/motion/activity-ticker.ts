/* Collapsed activity ticker cue.
 *
 * The vendored Habblaud feed has one non-control line, `.ui-feed__ticker`,
 * rendered as `<span aria-hidden="true">`. Text changes on it get a short
 * `data-fx-tick` pulse so the collapsed status bar reads as live.
 *
 * Deliberately NOT ported from the Stage 8 reference: the fresh-row flash on
 * `.ui-feed__item`. That element is the direct `<li>` wrapper of
 * `<button class="ui-feed__row">`, and Stage 9's zero-control-motion audit
 * counts animating a button's immediate wrapper as control motion. `.ui-toast`
 * and `.ui-tip` are also omitted for the same reason. `motion.css` never
 * mentions them; `motion-audit.test.ts` fails if they return.
 *
 * The pulse is a DOM attribute plus a CSS animation, not a frame loop: one
 * timer per tick, cleaned up with the controller.
 */

import { isReducedMotionActive } from "./reduced-motion";

export interface ActivityTickerOptions {
  /** How long `data-fx-tick` stays set. Matches the CSS entrance (220ms). */
  readonly pulseMs?: number;
}

const TICKER_SELECTOR = ".ui-feed__ticker";

/**
 * Observe the collapsed ticker and pulse it on text/list changes. Returns a
 * cleanup function; safe to call in a non-DOM environment.
 */
export function attachActivityTickerController(
  root: ParentNode | null = typeof document !== "undefined" ? document : null,
  options: ActivityTickerOptions = {},
): () => void {
  const pulseMs = options.pulseMs ?? 240;
  if (!root || typeof MutationObserver === "undefined"
      || typeof window === "undefined") {
    return () => {};
  }

  const ticker = root.querySelector(TICKER_SELECTOR);
  if (!ticker) {
    return () => {};
  }

  let timer: number | null = null;

  const pulse = () => {
    if (isReducedMotionActive(ticker)) {
      return;
    }
    ticker.setAttribute("data-fx-tick", "1");
    if (timer !== null) {
      window.clearTimeout(timer);
    }
    timer = window.setTimeout(() => {
      ticker.removeAttribute("data-fx-tick");
      timer = null;
    }, pulseMs);
  };

  const observer = new MutationObserver(pulse);
  observer.observe(ticker, {
    characterData: true,
    childList: true,
    subtree: true,
  });

  return () => {
    observer.disconnect();
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    ticker.removeAttribute("data-fx-tick");
  };
}
