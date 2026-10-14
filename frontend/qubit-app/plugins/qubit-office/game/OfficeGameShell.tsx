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
import { assembleWorld, type OfficeWorld } from "./WorldAssembler";
import type { OfficeTowerPayload } from "./types";
import { drawPlace } from "./scenes";
import { hotspotsFor, hitTest, applyIntent, type Intent } from "./InteractionController";

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
  /** The world to render. A `payload` prop is assembled here; a prebuilt world
   *  is accepted directly so tests and the F05 adapter can drive it. */
  payload?: OfficeTowerPayload | null;
  world?: OfficeWorld | null;
  /** Bumped by the caller on every accepted payload; drives world re-assembly. */
  revision?: number;
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
  load?: (hooks: OfficeGameShellProps["hooks"]) => {
    payload?: OfficeTowerPayload | null;
    revision?: number;
    portals?: Portal[];
    initialPlace?: Place;
  },
) {
  return function Office() {
    // The live data is loaded through the HOST's hooks, inside the render, so
    // the shell receives it as plain props and stays a pure renderer.
    const data = load ? load(hooks) : {};
    return OfficeGameShell({
      React: ReactLike, hooks,
      payload: data.payload ?? null,
      revision: data.revision ?? 0,
      portals: data.portals ?? [],
      initialPlace: data.initialPlace,
    });
  };
}

export function OfficeGameShell(props: OfficeGameShellProps) {
  const {
    React: R,
    initialCamera = { cx: 0, cy: 0, zoom: 1 },
    createScene = createPhaserHost,
    payload = null,
    world: worldProp = null,
    revision = 0,
    portals = [],
    initialPlace,
    onPlaceChange,
    onWorldCommand,
  } = props;
  // The hooks come from the HOST. See the header: no `react` import exists in
  // this module, so the dashboard's single React is the only one in play.
  const { useState, useEffect, useRef } = props.hooks;

  /**
   * The render model handed to the engine. A pure function of the world and
   * the viewpoint — the engine rasterises it and decides nothing.
   */
  function currentModel(): RenderModel {
    const w = worldRef.current;
    const { w: width, h: height } = sizeRef.current;
    return {
      revision: w ? w.revision : 0,
      draw: (ctx) => {
        if (!w) {
          // No world yet: say so rather than paint an invented office.
          ctx.save();
          ctx.fillStyle = "#0b0f16";
          ctx.fillRect(0, 0, width, height);
          ctx.fillStyle = "#8b97ad";
          ctx.font = "13px ui-sans-serif, system-ui, sans-serif";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText("Reading the office\u2026", width / 2, height / 2);
          ctx.restore();
          return;
        }
        drawPlace({
          ctx, world: w, width, height,
          place: navRef.current.viewpoint,
          travel: 0,
        });
      },
    };
  }

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
   * THE WORLD + THE DRAWING. The engine is handed a `RenderModel` whose `draw`
   * paints the CURRENT place from the CURRENT world — so the engine never
   * decides what is on screen, it only rasterises a pure function of
   * (payload revision, viewpoint). That is what keeps one renderer valid for
   * both engines and keeps C7 intact: no clock, no invented state.
   */
  const worldRef = useRef<OfficeWorld | null>(null);
  const selectedRef = useRef<number | null>(null);
  const sizeRef = useRef({ w: 880, h: 500 });
  /** Flips true once the stage element exists, so effects that need it re-run. */
  const [mounted, setMounted] = useState(0);

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
    // Paint the first frame if a payload already arrived.
    scene.step(currentModel(), scene.camera());
    force((n) => n + 1);
    // Announce that the stage exists. Effects that bind INPUT depend on this,
    // not on `[]`: a mount-only binding reads `stageRef.current === null` and
    // returns forever — the exact F02 bug, reproduced here in the shell.
    setMounted((n) => n + 1);
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
    //
    // `mounted` is deliberately NOT a dependency either: this effect is what
    // SETS it, and depending on it would remount the engine in a loop.
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
  }, [createScene, mounted]);

  // DATA CHANGE IS A STEP, NEVER A REBUILD. A new payload re-assembles the
  // world and repaints; it does not remount the engine (remounting on every
  // poll would be the 400 ms snapshot bug in a new costume).
  useEffect(() => {
    worldRef.current = worldProp ?? assembleWorld(payload, revision);
    const scene = sceneRef.current;
    if (scene) scene.step(currentModel(), scene.camera());
  }, [payload, worldProp, revision]);

  /**
   * THE MOUSE. A click resolves through the SAME hotspots the scenes draw and
   * the SAME reducer the keyboard uses, so a door you can see is a door you can
   * click and a door you can press.
   */
  useEffect(() => {
    const cv = canvasRef.current;
    // The stage div is the outer click surface; fall back to the shell so a
    // ref-timing difference can never leave the mouse completely unbound (the
    // F02 bug class, which this file has already been bitten by once).
    const stage = (stageRef.current ?? (cv && cv.parentElement)) as HTMLElement | null;
    if (!cv || !stage) return;
    const onPointerUp = (ev: PointerEvent) => {
      const target = (stage.querySelector("canvas") ?? cv) as HTMLElement;
      const r = target.getBoundingClientRect();
      const { w: width, h: height } = sizeRef.current;
      // Scene coordinates. The scenes draw in `sizeRef`'s space, and the canvas
      // is displayed at its natural size, so the map is a straight ratio — but
      // guarded, because a zero-size rect would divide by zero and silently
      // drop every click.
      if (r.width <= 0 || r.height <= 0) return;
      const x = (ev.clientX - r.left) / r.width * width;
      const y = (ev.clientY - r.top) / r.height * height;
      const world = worldRef.current;
      if (!world) return;
      const spots = hotspotsFor(navRef.current.viewpoint, world, width, height);
      const hit = hitTest(spots, x, y);
      if (!hit) return;
      const res = applyIntent(navRef.current, hit.intent, portalsRef.current,
                              world, selectedRef.current);
      navRef.current = res.nav;
      if (res.selected !== undefined) selectedRef.current = res.selected;
      if (res.refused) return;   // a refusal changes nothing on screen
      propsRef.current.onPlaceChange?.(res.nav.viewpoint);
      const scene = sceneRef.current;
      if (scene) scene.step(currentModel(), scene.camera());
    };
    stage.addEventListener("pointerup", onPointerUp);
    return () => stage.removeEventListener("pointerup", onPointerUp);
  }, [createScene, mounted]);

  // A resize changes the drawing surface. Observed, not polled.
  useEffect(() => {
    const cv = canvasRef.current;
    const stage = stageRef.current;
    if (!cv || !stage || typeof ResizeObserver === "undefined") return;
    const sync = () => {
      const box = stage.getBoundingClientRect();
      const w = Math.max(240, Math.floor(box.width) || 880);
      const h = Math.max(160, Math.floor(box.height) || 500);
      if (w === sizeRef.current.w && h === sizeRef.current.h) return;
      sizeRef.current = { w, h };
      const scene = sceneRef.current;
      if (scene) scene.step(currentModel(), scene.camera());
    };
    sync();   // first measurement, before any resize event fires
    const ro = new ResizeObserver(sync);
    ro.observe(stage);
    return () => ro.disconnect();
  }, [createScene, mounted]);

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
