/* F06 step 4 — the input-context layer.
 *
 * THE FAILURE THIS PREVENTS
 * -------------------------
 * F06's proof has two halves, and only the first was built: the reducer
 * round-trips every path, but "editing text never moves viewpoint or closes the
 * app" needs an input ROUTER. Without one, every keypress is a world keypress —
 * so typing `w` in a character's name field walks the viewpoint, `Escape` in a
 * search box leaves the building, and Backspace in a note navigates away from
 * an unsaved draft. That is the classic bug this layer exists to stop.
 *
 * HOW IT WORKS
 * ------------
 * Input is ROUTED BY THE FOCUSED CONTEXT, not by the key. The contexts form a
 * strict containment chain — `editor` and `text` are inside `modal`, which is
 * inside `panel`, which is inside `world` — and a key is offered to the
 * innermost context first. The FIRST context that claims the key wins and
 * nothing outer ever sees it. That is what makes "typing never navigates" a
 * structural property rather than a list of exceptions to remember.
 *
 * WHAT THIS FILE DELIBERATELY DOES NOT DO
 * --------------------------------------
 * It does not move the viewpoint. `routeInput` returns a NAV COMMAND and the
 * caller applies it through the F06 reducer — the same `travel`/`back`. Keeping
 * the two separate means this file can be tested without a world and the world
 * can be driven without a keyboard.
 */

export type InputContext = "world" | "panel" | "modal" | "text" | "editor";

/** Innermost last. A key is offered in reverse order and stops at the first taker. */
export const CONTEXT_ORDER: readonly InputContext[] =
  ["world", "panel", "modal", "text", "editor"] as const;

/**
 * True when `inner` is inside `outer` — the containment rule that decides which
 * contexts may take a key. `text` is treated as nested under `modal` only when
 * a modal is open, which the caller states by passing the active set.
 */
export function isWithin(inner: InputContext, outer: InputContext): boolean {
  const i = CONTEXT_ORDER.indexOf(inner);
  const o = CONTEXT_ORDER.indexOf(outer);
  return i >= 0 && o >= 0 && i >= o;
}

/** A key event, reduced to what the router actually needs. */
export interface KeyInput {
  key: string;
  /** True when a text field has the caret; the router must never read it. */
  hasFocus?: boolean;
  /** True for shift/ctrl/alt/meta combinations. */
  modified?: boolean;
  repeat?: boolean;
}

/** What the world may do with an unclaimed key. Never a DOM action. */
export type NavCommand =
  | { kind: "move"; dx: number; dy: number }
  | { kind: "zoom"; factor: number }
  | { kind: "travel"; direction: "enter" | "exit" | "up" | "down" }
  | { kind: "back" }
  | { kind: "select"; delta: number }
  | { kind: "none" };

export interface RouteResult {
  /** Which context consumed the key. `null` means nothing did. */
  handledBy: InputContext | null;
  /** Present only when the WORLD consumed it. */
  nav: NavCommand | null;
  /** The key the caller must pass to the field; never swallowed. */
  passThrough: boolean;
}

/** The contexts currently able to receive input, innermost last. */
export interface InputState {
  active: InputContext[];
}

/** Keys that always mean exactly one thing, whatever has focus. */
const HARD_KEYS: Record<string, NavCommand> = {
  Enter: { kind: "travel", direction: "enter" },
  Escape: { kind: "back" },
};

/** World movement keys. `w/a/s/d` and the arrows. */
const MOVE_KEYS: Record<string, { dx: number; dy: number }> = {
  w: { dx: 0, dy: -1 },
  a: { dx: -1, dy: 0 },
  s: { dx: 0, dy: 1 },
  d: { dx: 1, dy: 0 },
  ArrowUp: { dx: 0, dy: -1 },
  ArrowDown: { dx: 0, dy: 1 },
  ArrowLeft: { dx: -1, dy: 0 },
  ArrowRight: { dx: 1, dy: 0 },
  "-": { dx: 0, dy: 0 },
  "=": { dx: 0, dy: 0 },
};

/**
 * Best-effort context detection from the DOM. Used ONLY to seed state; the
 * router itself takes `InputState` so it stays testable without a document.
 */
export function detectContext(target: unknown): InputContext {
  const el = target as { tagName?: string; isContentEditable?: boolean;
    getAttribute?: (n: string) => string | null } | null;
  if (!el || typeof el !== "object") return "world";
  if (el.isContentEditable) return "text";
  const tag = String(el.tagName || "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "INPUT") {
    // A contenteditable-backed editor is its own context; a plain field is text.
    return el.getAttribute?.("data-office-editor") !== null && el.getAttribute
      ? "editor"
      : "text";
  }
  if (tag === "BUTTON" || tag === "SELECT") return "panel";
  const modal = el.getAttribute?.("[role]") ?? el.getAttribute?.("role");
  if (modal === "dialog") return "modal";
  return "world";
}

/**
 * ROUTE ONE KEY. The innermost active context that claims it wins.
 *
 * The rule that matters: if ANY text-bearing context is active (`text` or
 * `editor`), the world is not allowed to interpret the key at all — it is
 * passed through to the field. That single guard is what stops typing from
 * walking the viewpoint.
 */
export function routeInput(state: InputState, input: KeyInput): RouteResult {
  const block = { handledBy: null, nav: null, passThrough: false } as const;
  const active = CONTEXT_ORDER.filter((c) => state.active.includes(c));
  if (active.length === 0) return { ...block };

  const typing = active.includes("text") || active.includes("editor");
  if (input.repeat) {
    // A held key must not repeat a navigation. A held `w` in a field is still
    // the field's business.
    if (!typing) return { handledBy: null, nav: null, passThrough: false };
    return { handledBy: null, nav: null, passThrough: true };
  }
  if (input.modified) {
    // Never steal a chord: Ctrl/Cmd+A is select-all, not "move left".
    return typing
      ? { handledBy: null, nav: null, passThrough: true }
      : { handledBy: null, nav: null, passThrough: false };
  }

  // 1. A text-bearing context owns EVERYTHING except the hard keys it
  //    explicitly declines. Escape in a field must not leave the building.
  if (typing) {
    return { handledBy: null, nav: null, passThrough: true };
  }

  // 2. Modal: Escape closes, Enter confirms; movement is swallowed.
  if (active.includes("modal")) {
    if (input.key === "Escape") {
      return { handledBy: "modal", nav: { kind: "back" }, passThrough: false };
    }
    return { handledBy: "modal", nav: null, passThrough: false };
  }

  // 3. Panel: Escape closes; movement is swallowed so it does not walk the
  //    world. `world` is ALWAYS in the active set (it is the base context), so
  //    the test cannot be "world is absent" — it is whether a panel sits ABOVE
  //    the world, which is exactly `active.length > 1` with panel innermost.
  if (active.includes("panel") && active[active.length - 1] === "panel") {
    if (input.key === "Escape") {
      return { handledBy: "panel", nav: { kind: "back" }, passThrough: false };
    }
    return { handledBy: "panel", nav: null, passThrough: false };
  }

  // 4. World: the only context that may navigate.
  const hard = HARD_KEYS[input.key];
  if (hard) {
    return { handledBy: "world", nav: hard, passThrough: false };
  }
  const move = MOVE_KEYS[input.key];
  if (move) {
    if (input.key === "-" || input.key === "=") {
      return {
        handledBy: "world",
        nav: { kind: "zoom", factor: input.key === "=" ? 1.1 : 0.9 },
        passThrough: false,
      };
    }
    return { handledBy: "world", nav: { kind: "move", ...move },
             passThrough: false };
  }
  if (input.key === "Tab") {
    return { handledBy: "world", nav: { kind: "select", delta: 1 },
             passThrough: false };
  }
  // An unclaimed key in the world is nobody's business.
  return { handledBy: null, nav: null, passThrough: false };
}

/** Push a context. Idempotent, so a re-render cannot stack duplicates. */
export function pushContext(state: InputState, c: InputContext): InputState {
  if (state.active.includes(c)) return state;
  return { active: [...state.active, c] };
}

/** Pop one context off the top of the containment chain. */
export function popContext(state: InputState, c: InputContext): InputState {
  return { active: state.active.filter((x) => x !== c) };
}
