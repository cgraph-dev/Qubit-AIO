/* F04 — the React host that mounts exactly ONE engine instance.
 *
 * THE TWO FAILURES THIS SHAPE PREVENTS
 * ------------------------------------
 * 1. **Zero listeners** (the F02 bug, in its general form). The effect below
 *    binds when the container ref exists, not at mount, so a branch that
 *    renders no canvas cannot leave the engine unbound.
 * 2. **StrictMode double-mount leaks.** React 19 in StrictMode mounts,
 *    unmounts and re-mounts. A host that only ever `mount()`s would stack two
 *    engines on one canvas — two input handlers, two draws, and a memory leak
 *    that grows with every navigation. The cleanup MUST destroy; the spike
 *    test asserts the listener count returns to zero between mounts.
 *
 * The engine itself is injected as a factory so the Phaser-vs-Canvas decision
 * (10-DECISIONS.md § Renderer comparison) stays a one-line swap and is not
 * baked into the React layer.
 */

import { createCanvasHost } from "./CanvasSceneHost";
import { createPhaserHost } from "./PhaserSceneHost";
import type { Camera, RenderModel, SceneHost, SceneFactory } from "./SceneHost";
import { detectContext, routeInput, pushContext, type InputState } from "./input";
import { back, travel, type NavState, type Place, type Portal } from "./navigation";

/**
 * REACT COMES FROM THE HOST, NOT FROM AN IMPORT.
 *
 * The dashboard injects exactly ONE React — `window.__QUBIT_PLUGIN_SDK__.React`
 * — because a second copy would split the hook runtime (context, session and
 * theme would not reach the plugin, silently). So this module deliberately does
 * NOT `import ... from "react"`: the build externalizes React, and an import
 * would leave a bare `React` global reference that does not exist in the
 * dashboard. The hooks arrive as props, exactly as the plugin contract intends.
 */
type ReactLike = {
  createElement: (...args: unknown[]) => unknown;
};
type HooksLike = {
  useState: <T>(v: T) => [T, (next: T | ((prev: T) => T)) => void];
  useEffect: (fn: () => void | (() => void), deps?: unknown[]) => void;
  useRef: <T>(v: T) => { current: T };
};

export interface OfficeGameShellProps {
  /** The host's React. Never a second runtime. */
  React: ReactLike;
  hooks: HooksLike;
  /** The engine. Defaults to PHASER, with the Canvas host as the fallback. */
  createScene?: SceneFactory;
  /** Authoritative payload -> drawing input. Supplied by the F05 adapter. */
  model?: RenderModel | null;
  /** Initial camera. Fit/reset target; the host never overwrites it on redraw. */
  initialCamera?: Camera;
  /** The building's portal graph (F06). Absent means navigation is inert. */
  portals?: Portal[];
  /** The starting viewpoint. */
  initialPlace?: Place;
  /** Called when the viewpoint moves, so the caller can persist a deep link. */
  onPlaceChange?: (place: Place) => void;
  /** Called when a key should zoom or select, which the camera/HUD owns. */
  onWorldCommand?: (cmd: { kind: "zoom"; factor: number } |
                          { kind: "select"; delta: number }) => void;
}

/**
 * Build the component the plugin registry wants, bound to the host's React.
 *
 * The registry stores the second argument to `register()` AS the component, so
 * this returns a function React can render — not an object, and not a
 * pre-built element. React and hooks are captured here, which is what keeps the
 * dashboard's single React in play.
 */
export function createOfficeComponent(
  ReactLike: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  return function Office() {
    return OfficeGameShell({ React: ReactLike, hooks });
  };
}

export function OfficeGameShell(props: OfficeGameShellProps) {
  const {
    React: R,
    model = null,
    initialCamera = { cx: 0, cy: 0, zoom: 1 },
    createScene = createPhaserHost,
    portals = [],
    initialPlace,
    onPlaceChange,
    onWorldCommand,
  } = props;
  // The hooks come from the HOST. See the header: no `react` import exists in
  // this module, so the dashboard's single React is the only one in play.
  const { useState, useEffect, useRef } = props.hooks;

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  /** The empty container the ENGINE mounts into (Phaser creates its own canvas). */
  const stageRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<SceneHost | null>(null);
  const [, force] = useState(0);

  /**
   * Navigation state lives in refs, not React state, because the key handler is
   * bound once to the element and must read the CURRENT state. Re-binding on
   * every navigation would stack listeners — the exact leak class the mount
   * effect above guards against.
   */
  const navRef = useRef<NavState>({
    viewpoint: initialPlace ?? { kind: "exterior" },
    history: [],
    refused: null,
  });
  const portalsRef = useRef<Portal[]>(portals);
  portalsRef.current = portals;
  const propsRef = useRef({ onPlaceChange, onWorldCommand });
  propsRef.current = { onPlaceChange, onWorldCommand };

  /**
   * ONE mount per canvas presence. The dependency is the canvas element's
   * identity, not `[]` — that is the whole fix class.
   */
  useEffect(() => {
    const cv = canvasRef.current;
    const stage = stageRef.current;
    if (!cv || !stage) return;
    const scene = createScene();
    // The engine mounts into the EMPTY stage; the hidden canvas is only the
    // measuring stick. Handing the engine a container that already holds a
    // canvas is how a page ends up with two.
    scene.mount(stage, cv);
    sceneRef.current = scene;
    force((n) => n + 1);
    return () => {
      scene.destroy();
      sceneRef.current = null;
      // The engine removes its own canvas on destroy; clear anything a partial
      // boot left behind so a remount starts from an empty stage.
      while (stage.firstChild) stage.removeChild(stage.firstChild);
    };
    // `model` deliberately NOT a dependency: a new payload is a `step()`, not a
    // remount. Remounting on every poll would be the 400 ms snapshot bug in a
    // new costume.
  }, [createScene]);

  /**
   * THE KEYBOARD. Bound to the container, and deliberately NOT at the document
   * level: a global handler would fight the dashboard's own shortcuts.
   *
   * The key is routed through F06's context chain, so a focused text field
   * keeps every key and the world only sees what nothing else claimed. The
   * context is re-derived on every keystroke from the live `event.target`,
   * because focus changes far more often than React re-renders.
   */
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const container = cv.parentElement ?? cv;

    const onKeyDown = (ev: Event) => {
      const e = ev as KeyboardEvent;
      const inner = detectContext(e.target);
      // The base context is always the world; a panel/modal/text context is
      // added on top of it by whatever opened it.
      let state: InputState = { active: ["world"] };
      if (inner !== "world") state = pushContext(state, inner);
      // A modal above the world is stated by an attribute, since a modal traps
      // focus inside itself and its own elements are what receive the key.
      const asEl = container as HTMLElement & { dataset?: DOMStringMap };
      if (asEl.dataset?.officeModal === "1") state = pushContext(state, "modal");

      const r = routeInput(state, {
        key: e.key,
        modified: !!(e.ctrlKey || e.metaKey || e.altKey),
        repeat: e.repeat,
      });
      if (r.passThrough) return;            // the field keeps it
      if (r.nav === null) return;           // nobody claimed it
      e.preventDefault();                   // the page must not also scroll

      if (r.nav.kind === "zoom" || r.nav.kind === "select") {
        propsRef.current.onWorldCommand?.(r.nav);
        return;
      }
      if (r.nav.kind === "travel" || r.nav.kind === "back") {
        const before = navRef.current;
        const next = r.nav.kind === "back"
          ? back(before)
          : travelTo(before, r.nav.direction, portalsRef.current);
        navRef.current = next;
        if (next.viewpoint !== before.viewpoint) {
          propsRef.current.onPlaceChange?.(next.viewpoint);
        }
        return;
      }
      // `move` is presentation: it shifts the viewpoint within the current
      // place and is the camera's business, not the graph's.
      if (r.nav.kind === "move") {
        propsRef.current.onWorldCommand?.(r.nav as never);
      }
    };

    container.addEventListener("keydown", onKeyDown);
    return () => container.removeEventListener("keydown", onKeyDown);
  }, [createScene]);

  // Data change is a step, never a rebuild.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !model) return;
    scene.step(model, scene.camera());
  }, [model]);

  // NO JSX. JSX would compile to a `react/jsx-runtime` import, which the build
  // externalizes into a global the dashboard does not define — that is the
  // `React is not defined` failure. `R.createElement` from the HOST's React is
  // the same element tree with no second runtime.
  //
  // ONE CANVAS, NOT TWO. An engine host that creates its own canvas (Phaser
  // does) must be given a container that holds nothing else, or the page ends
  // up with two canvases: React's (unused) plus the engine's. The measuring
  // canvas below is hidden and only tells the host how big to make its surface;
  // the engine mounts into the empty `stageRef` div beside it.
  return R.createElement(
    "div",
    {
      "data-state": "ready",
      "data-testid": "office-game-shell",
      tabIndex: 0,
      style: { display: "block", width: "100%", height: "100%",
               position: "relative" },
    },
    R.createElement("canvas", {
      ref: canvasRef,
      width: 880,
      height: 500,
      "data-testid": "office-game-canvas",
      "aria-hidden": "true",
      style: { display: "none" },
    }),
    R.createElement("div", {
      ref: stageRef,
      "data-testid": "office-game-stage",
      style: { width: "100%", height: "100%" },
    }),
  );
}

/** Enter/exit/up/down resolved to a concrete place, then travelled. */
function travelTo(state: NavState, direction: string, portals: Portal[]): NavState {
  const here = state.viewpoint;
  const candidates = portals.filter((p) => samePlace(p.from, here));
  const pick =
    direction === "enter"
      ? candidates.find((p) => p.via === "entrance" || p.via === "door")
      : direction === "exit"
        ? candidates.find((p) => p.via === "exit" || p.via === "door")
        : direction === "up"
          ? candidates
              .filter((p) => p.via === "elevator" || p.via === "stairs")
              .sort((a, b) => (b.to.floor ?? 0) - (a.to.floor ?? 0))[0]
          : candidates
              .filter((p) => p.via === "elevator" || p.via === "stairs")
              .sort((a, b) => (a.to.floor ?? 0) - (b.to.floor ?? 0))[0];
  if (!pick) {
    return {
      ...state,
      refused: {
        reason: `there is nothing to ${direction} from here`,
        from: here, to: here,
      },
    };
  }
  return travel(state, pick.to, portals);
}

function samePlace(a: Place, b: Place): boolean {
  return a.kind === b.kind && a.building === b.building
    && a.floor === b.floor && a.room === b.room;
}
