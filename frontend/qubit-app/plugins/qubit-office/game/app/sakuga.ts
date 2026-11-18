/* Sakuga VFX for the Qubit office HOST shell (React-free).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The office plugin bundle externalises React (`window.React`) and receives the
 * host's React in `R`; importing `@qubit/shared/sakuga` (whose index re-exports
 * React wrappers) would pull a second React into the plugin. So this module
 * imports only the React-free leaf files (loader + effect bus) and builds the
 * `sakuga-button` element with the host's own `R.createElement`.
 *
 * The button is the upstream custom element: its shadow DOM owns a real native
 * <button>, keeps keyboard/screen-reader behaviour, and draws the impact frame
 * itself. We keep the existing role/label/testid/class contract on the light-DOM
 * host so the office's tests and styles continue to find the same controls.
 */
import {
  configureSakuga,
  loadSakugaComponent,
} from "../../../../../qubit-shared/src/sakuga/loader";
import {
  emitFx,
  fireFx,
  type FxCue,
  type FxOptions,
} from "../../../../../qubit-shared/src/sakuga/fx";

type OfficeReact = {
  createElement: (...args: unknown[]) => unknown;
};

declare global {
  interface Window {
    /** Optional host override (Electron webview / subpath deployments). */
    __QUBIT_SAKUGA_BASE__?: string;
  }
}

let configured = false;

/** Point the loader at the served `/sakuga-vfx` assets, once per document. */
export function configureOfficeSakuga(): void {
  if (configured) return;
  configured = true;
  const win = typeof window === "undefined" ? null : window;
  const raw = win?.__QUBIT_SAKUGA_BASE__ || "/sakuga-vfx";
  const base = String(raw).replace(/\/+$/, "") || "/sakuga-vfx";
  try {
    configureSakuga({ base });
  } catch {
    // Presentation must never break the shell; a failed configure leaves the
    // loader's default (/sakuga-vfx), which is where the dashboard serves it.
  }
}

/**
 * A `sakuga-button` with the caller's attributes preserved.
 *
 * `props` are passed through unchanged (role, label, testid, className, type,
 * disabled, aria-*, data-*), so every existing selector and assertion keeps
 * working. The component script is loaded lazily; until it is defined the host
 * element is inert but still carries the accessible role and label.
 */
export function sakugaButton(
  R: OfficeReact,
  props: Record<string, unknown> | null,
  ...children: unknown[]
): unknown {
  configureOfficeSakuga();
  try {
    void loadSakugaComponent("button").catch(() => {
      // A failed component leaves the rest of the office usable.
    });
  } catch {
    // No DOM (unit harness): the recorded element still carries every prop.
  }
  const next: Record<string, unknown> = { ...(props ?? {}) };
  if (!next.role) next.role = "button";
  return R.createElement("sakuga-button", next, ...children);
}

/** Fire one named cue in the dashboard document (the office shell). */
export function officeFx(cue: FxCue | null, options: FxOptions = {}): void {
  if (!cue) return;
  configureOfficeSakuga();
  void emitFx(cue, options);
}

/** Dispatch through the product-wide `qubit:fx` bus (dashboard bridge). */
export function officeFxEvent(cue: FxCue, options: FxOptions = {}): void {
  fireFx(cue, options);
}

/**
 * Map a tower animation to a Sakuga cue.
 *
 * The tower's animation vocabulary is the source: `done`/`settling` are a
 * completed turn/task, `stopped` a failed run, `blocked` a denied permission.
 * Everything else is ordinary work and produces no celebratory cue.
 */
export function officeFxForAnimation(animation: unknown): FxCue | null {
  switch (String(animation || "")) {
    case "done":
    case "settling":
      return "success";
    case "stopped":
      return "error";
    case "blocked":
      return "warning";
    default:
      return null;
  }
}

/** Map an office `fx` record's event_type/animation to a shell cue. */
export function officeFxForEvent(eventType: unknown, animation?: unknown): FxCue | null {
  const type = String(eventType || "");
  if (type.includes("approval") || type.includes("escalation")) return "approval";
  if (type.includes("failed") || type.includes("denied")) return "error";
  if (type.includes("unknown") || type.includes("warning")) return "warning";
  if (type.includes("completed") || type.includes("settled")) return "success";
  return officeFxForAnimation(animation);
}
