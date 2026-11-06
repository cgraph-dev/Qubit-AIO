/* The HUD, opened from inside the Office — by REUSING the HUD, not copying it.
 *
 * WHY NOT AN IFRAME, ALTHOUGH THE HABBLAUD APP IS ONE
 * --------------------------------------------------
 * The Office stays read-only because its shell issues no request of its own
 * (C4: every mutation goes through the staged-op confirmation journal). Here we
 * must let the HUD's own GET reads run from inside the office page — so the
 * question is which mechanism lets them in WITHOUT the office taking part, and
 * which one we can PROVE keeps writes out.
 *
 *   iframe of /qubit-hud — the server refuses it. `webui.py::security_headers`
 *   emits `X-Frame-Options: DENY` and `frame-ancestors 'none'` for every
 *   document but the Habblaud one (`_serve_qubit_index` never sets
 *   `_allow_same_origin_frame`); measured live, `/qubit-hud` answers DENY. It
 *   would also give the panels a second React copy, a second auth seam (the
 *   iframe's own `fetchJSON`), and an opaque window we cannot gate.
 *
 *   The HUD's own registered component — registered by its unchanged bundle
 *   (`/dashboard-plugins/qubit-hud/qubit-hud.js`) and reading through the
 *   HOST's `fetchJSON`. Same React (the SDK's), so no hook-split; the office
 *   shell itself still issues no request; and the panel hands the HUD the one
 *   client the panels are given.
 *
 * THE GATE IS THE POINT
 * ---------------------
 * `readOnlyFetch` refuses anything but a bodyless GET BEFORE the host client is
 * touched, so a write can never reach the session or the staged-op journal from
 * this surface: the office is not where the first exception is made, and the
 * HUD inside it is not either. `Hud` is handed the wrapped client as a
 * `fetchJSON` prop for future tabs; today's 7 tabs are GET-only by construction
 * and keep the client their IIFE captured, which this module cannot reach —
 * that is stated rather than papered over.
 *
 * RESOLVING THE COMPONENT WITHOUT A LOOKUP
 * ----------------------------------------
 * `window.__QUBIT_PLUGINS__` describes itself as `{ register, registerSlot }`
 * (registry.ts::exposePluginSDK) — no `get`. So the component is taken from the
 * ONE place that has it: the HUD's own `register` call. `watch()` installs a
 * pass-through wrapper ONCE; the host still receives every call it would have
 * received, in order, with the same arguments.
 */

/** The HUD's registered plugin name — its own manifest's `name`. */
export const HUD_PLUGIN = "qubit-hud";

/** Its served bundle directory (an unchanged artifact; we do not build it). */
export const HUD_ASSET_ROOT = "/dashboard-plugins/qubit-hud";
export const HUD_SCRIPT = `${HUD_ASSET_ROOT}/qubit-hud.js`;

/** The `fetchJSON` contract, as the SDK declares it (`plugins/sdk.d.ts`). */
export type FetchJSONLike = (
  url: string,
  init?: { method?: string; body?: unknown },
  options?: unknown,
) => Promise<unknown>;

export type HudComponent = (props: { fetchJSON?: FetchJSONLike }) => unknown;
export type HudRegistration = (name: string, component: HudComponent) => void;

/**
 * The two things the HUD dock needs from its host. Defined HERE, not on the
 * game shell's props: the game shell is the rollback renderer and carries no
 * HUD affordance, so giving it HUD props would be props nothing reads.
 */
export interface HudDockProps {
  /** Where the HUD's registration is captured (entry.ts installs it). */
  hudTap?: HudTap;
  /** The host's authenticated client. Wrapped by `readOnlyFetch`, never raw. */
  fetchJSON?: FetchJSONLike;
}

/**
 * The dock's host wiring, set ONCE by `entry.ts` before any component renders.
 *
 * WHY MODULE STATE AND NOT A PROP: the plugin contract renders a registered
 * component with NO props (`PluginPage.tsx`), and `createOfficeApp(R, H)` is the
 * factory the entry's own test pins by signature. A module the bundle already
 * contains is the seam that costs neither: there is one office page per
 * document, so there is nothing to disambiguate.
 */
let dockProps: HudDockProps = {};

export function setHudDockProps(props: HudDockProps): void {
  dockProps = props;
}

export function hudDockProps(): HudDockProps {
  return dockProps;
}

/** The registry handle the dashboard exposes — `register` is all we use. */
export interface HudRegistry {
  register?: HudRegistration;
}

/** What `watch` remembers, and what it re-points on a remount. */
export interface HudTap {
  /** The HUD's component, once its bundle has registered. */
  component: HudComponent | null;
  /** Every reader that wants to re-render when the HUD arrives. */
  listeners: Set<() => void>;
}

/**
 * Watch a registry for the HUD's registration, without changing what the host
 * receives. Idempotent: the wrapper installs once, installs no second copy, and
 * re-points at this tap so a later page load captures into the live state.
 */
export function watchHudRegistration(
  registry: HudRegistry | null | undefined,
  tap: HudTap,
): HudTap {
  if (!registry || typeof registry.register !== "function") return tap;
  const current = registry.register as HudRegistration & { __hudTap?: HudTap };
  if (current.__hudTap) {
    current.__hudTap.listeners = tap.listeners;
    return current.__hudTap;
  }
  const real = current;
  const wrapped = ((name: string, component: HudComponent) => {
    if (name === HUD_PLUGIN) {
      wrapped.__hudTap!.component = component;
      wrapped.__hudTap!.listeners.forEach((fn) => fn());
    }
    real(name, component);
  }) as HudRegistration & { __hudTap?: HudTap };
  wrapped.__hudTap = tap;
  try {
    registry.register = wrapped;
  } catch {
    // A frozen registry: the HUD is simply unresolvable from here, and the
    // panel says so by name instead of rendering nothing.
  }
  return tap;
}

/**
 * Wrap the host's `fetchJSON` so only a body-less GET gets through.
 *
 * A refusal is an Error with a `disabled: true` marker rather than a silent
 * resolve: a tab that tried to write must SAY so, not paint an empty success.
 */
export function readOnlyFetch(inner: FetchJSONLike): FetchJSONLike {
  const blocked = (what: string) => Object.assign(
    new Error(`The office HUD is read-only: ${what} is not sent from this page.`),
    { disabled: true },
  );
  return (url, init) => {
    const method = String(init?.method ?? "GET").toUpperCase();
    if (method !== "GET") throw blocked(`a ${method} request`);
    if (init?.body !== undefined && init?.body !== null) {
      throw blocked("a GET carrying a request body");
    }
    return inner(url, init);
  };
}

/** REACT AND HOOKS COME FROM THE HOST — same rule as the rest of the plugin. */
export interface CreateHudPanelArgs {
  React: { createElement: (...args: unknown[]) => unknown };
  hooks: {
    useState: <T>(v: T) => [T, (next: T | ((prev: T) => T)) => void];
    useEffect: (fn: () => void | (() => void), deps?: unknown[]) => void;
  };
  /** The host's authenticated GET client. Wrapped, never used raw. */
  fetchJSON?: FetchJSONLike;
  /** The tap the HUD's registration lands in (searchable, not captured). */
  tap: HudTap;
  /** A component known up front (a test); otherwise the tap supplies it. */
  component?: HudComponent | null;
}

/**
 * The HUD as a docked panel, with the states that can lie made visible: a HUD
 * that never registered, and a page with no read client, are each NAMED.
 */
export function createHudPanel(args: CreateHudPanelArgs) {
  const { React: R, hooks, fetchJSON, tap } = args;
  const read = fetchJSON ? readOnlyFetch(fetchJSON) : null;

  return function HudPanel() {
    // A HUD that registers AFTER this panel mounted must still appear: the tap
    // calls every listener, and `force` is what turns that into a render.
    const [, force] = hooks.useState(0);
    hooks.useEffect(() => {
      const bump = () => force((n) => n + 1);
      tap.listeners.add(bump);
      return () => { tap.listeners.delete(bump); };
    }, []);

    const Hud = args.component ?? tap.component;
    const note = (id: string, text: string, role = "status") =>
      R.createElement("div", {
        key: id, className: "office-game__panel", "data-testid": id,
        role, "aria-live": "polite",
      }, text);
    const box = (state: string, child: unknown) =>
      R.createElement("div", {
        className: "office-game__hud", "data-testid": "office-game-hud-panel",
        "data-state": state,
      }, child);

    if (!Hud) {
      return box("error", note("office-game-hud-error",
        `The HUD is not available on this page: its bundle (${HUD_SCRIPT}) ` +
        `has not registered. Its panels are not shown rather than shown empty.`,
        "alert"));
    }
    if (!read) {
      return box("error", note("office-game-hud-error",
        "The HUD needs the dashboard's read client, and none is available on " +
        "this page. Its panels are not shown rather than shown empty.", "alert"));
    }
    return box("ready", R.createElement(Hud, { key: "hud", fetchJSON: read }));
  };
}
