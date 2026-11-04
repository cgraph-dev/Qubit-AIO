/**
 * Reusable dialog focus management.
 *
 * A modal is only modal for keyboard and assistive-tech users when focus
 * cannot escape it. Radix-based dialogs get this from the primitive; the
 * dashboard also has hand-rolled dialogs (Profiles, Models, Webhooks, Cron,
 * MCP, Channels and the shared ConfirmDialog), so they share the same small
 * trap here instead of each re-implementing Tab handling.
 */

export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type=\"hidden\"])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex=\"-1\"])",
  "[contenteditable=\"true\"]",
].join(",");

/**
 * Every tabbable descendant that is not explicitly hidden from assistive
 * technology. Deliberately avoids layout queries (`offsetParent`,
 * `getClientRects`) because jsdom has no layout engine and the trap must be
 * testable there.
 */
export function focusableElements(
  container: HTMLElement | null | undefined,
): HTMLElement[] {
  if (!container) return [];
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((el) => {
    if (el.hasAttribute("disabled")) return false;
    if (el.getAttribute("aria-hidden") === "true") return false;
    if (el.hidden) return false;
    return true;
  });
}

/** First tabbable element, or the container itself when it has none. */
export function firstFocusable(
  container: HTMLElement | null | undefined,
): HTMLElement | null {
  if (!container) return null;
  return focusableElements(container)[0] ?? container;
}

/**
 * Wrap Tab / Shift+Tab inside `container`.
 *
 * Returns true when the event was handled. Focus that has already escaped the
 * container is pulled back to the first/last element instead of being allowed
 * to wander through the page behind the modal.
 */
export function trapTabKey(
  container: HTMLElement | null | undefined,
  event: Pick<KeyboardEvent, "key" | "shiftKey" | "preventDefault">,
  active: HTMLElement | null = typeof document === "undefined"
    ? null
    : (document.activeElement as HTMLElement | null),
): boolean {
  if (event.key !== "Tab" || !container) return false;

  const focusable = focusableElements(container);
  if (focusable.length === 0) {
    event.preventDefault();
    container.focus?.();
    return true;
  }

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const inside = active ? container.contains(active) : false;

  if (!inside) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return true;
  }

  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
    return true;
  }

  if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
    return true;
  }

  return false;
}
