/* F08 — the panel host: one primary panel, one drawer, exclusive modals.
 *
 * WHY A POLICY AND NOT A WINDOW MANAGER
 * ------------------------------------
 * The audited baseline's failure was "scattered dashboard widgets" (P08):
 * unlimited draggable windows reproduce exactly that. So the shell allows:
 *
 *   world  — always present
 *   panel  — ONE at a time, beside the world, non-blocking
 *   drawer — ONE at a time, contextual, closes on Escape
 *   modal  — EXCLUSIVE: dims the world, traps focus, and there is at most one
 *
 * KEYBOARD CORRECTNESS, WHICH IS THE PART EASY TO SKIP
 * ----------------------------------------------------
 * A modal that does not trap focus is not a modal — Tab walks out into the
 * page behind it and a keyboard user is lost. And a modal that does not
 * RESTORE focus on close dumps the user at the top of the document. Both are
 * implemented here, and both are tested, because "we set role=dialog" is a
 * claim rather than a behaviour.
 */

export type PanelKind = "none" | "character" | "work" | "settings" | "help";
export type ModalKind = "none" | "confirm" | "error";

export interface PanelState {
  panel: PanelKind;
  drawer: PanelKind;
  modal: ModalKind;
  /** The element focus returns to when the modal closes. */
  returnFocusTo: HTMLElement | null;
}

export function initialPanelState(): PanelState {
  return { panel: "none", drawer: "none", modal: "none", returnFocusTo: null };
}

/**
 * Open a panel. Opening a MODAL records where focus came from and clears the
 * drawer (a drawer behind a modal is unreachable, and leaving it open means a
 * Tab can still find it).
 */
export function openPanel(
  state: PanelState,
  kind: PanelKind,
  asDrawer: boolean,
  from?: HTMLElement | null,
): PanelState {
  if (asDrawer) return { ...state, drawer: kind };
  return { ...state, panel: kind };
}

export function openModal(
  state: PanelState,
  kind: ModalKind,
  from?: HTMLElement | null,
): PanelState {
  return { ...state, modal: kind, drawer: "none", returnFocusTo: from ?? null };
}

/** Close the topmost thing. Modal first, then drawer, then panel. */
export function closeTop(state: PanelState): PanelState {
  if (state.modal !== "none") {
    return { ...state, modal: "none" };
  }
  if (state.drawer !== "none") return { ...state, drawer: "none" };
  if (state.panel !== "none") return { ...state, panel: "none" };
  return state;
}

export function isModalOpen(state: PanelState): boolean {
  return state.modal !== "none";
}

/**
 * The focusable elements inside a container, in tab order.
 *
 * Uses the same selector as the platform convention so a disabled or hidden
 * control is not a trap exit. `tabindex="-1"` is deliberately excluded: it is
 * programmatically focusable but not a Tab stop.
 */
export function focusables(root: HTMLElement): HTMLElement[] {
  const sel = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");
  return Array.from(root.querySelectorAll<HTMLElement>(sel)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

/**
 * Move focus for one Tab press, wrapping at the ends. Returns the element that
 * should receive focus, so the caller does not have to know the DOM.
 *
 * `shift` reverses. An empty container returns null and the caller should keep
 * focus on the container itself — a trap with no exits must still hold.
 */
export function nextFocus(
  root: HTMLElement, current: Element | null, shift: boolean,
): HTMLElement | null {
  const list = focusables(root);
  if (list.length === 0) return null;
  const idx = current ? list.indexOf(current as HTMLElement) : -1;
  if (idx === -1) return shift ? list[list.length - 1] : list[0];
  const next = shift
    ? (idx - 1 + list.length) % list.length
    : (idx + 1) % list.length;
  return list[next];
}

/** Where focus should go when a modal closes. */
export function focusTargetOnClose(state: PanelState): HTMLElement | null {
  return state.returnFocusTo;
}

/**
 * The input contexts the F06 router needs, derived from the panel state.
 *
 * This is the bridge the F06 work deliberately left out: the router takes a
 * context set, and this decides what that set IS from the panels currently
 * open. A panel above the world swallows movement; a modal swallows everything
 * but Escape; a focused field keeps every key.
 */
export function contextsFor(
  state: PanelState, focusedIsText: boolean,
): Array<"world" | "panel" | "modal" | "text" | "editor"> {
  const out: Array<"world" | "panel" | "modal" | "text" | "editor"> = ["world"];
  if (state.panel !== "none" || state.drawer !== "none") out.push("panel");
  if (isModalOpen(state)) out.push("modal");
  if (focusedIsText) out.push("text");
  return out;
}
