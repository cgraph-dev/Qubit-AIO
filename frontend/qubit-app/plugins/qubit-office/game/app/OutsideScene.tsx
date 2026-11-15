/* The visual outside-world scene for Qubit Office.
 *
 * WHAT THIS IS
 * ------------
 * A canvas renderer for the campus exterior that already exists as data in
 * `outside-world.ts`. It draws the building entrance, paths, ground, garden,
 * props, parking, weather / day-night / season cues and the payload's own
 * elevator/stairs floor links. The semantic `OutsideWorld` panel stays beside
 * it as the keyboard/screen-reader companion.
 *
 * REAL ART IS OPTIONAL AND NEVER FAKED
 * ------------------------------------
 * Paid LimeZu / Serene Village art is not committed. When the active office
 * theme pack carries `outside/scene.json` (served by the existing theme static
 * route), the scene paints those tiles. Otherwise it draws a clearly labelled
 * procedural scene. A missing pack is the normal state, not an error banner.
 *
 * The component never invents a zone, a chunk, an occupant or a floor. If the
 * payload does not carry it, it is not on the canvas.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { hostBasePath } from "./business-api";
import {
  type OutsideFloorLink, type OutsideWorld, type OutsideWorldView,
  type OutsideZone, currentEnvironment, floorHref,
} from "./outside-world";
import {
  type OutsideArtLoadResult, type OutsideSceneArt, SCENE_TILE_PX,
  buildOutsideScene, drawOutsideScene, loadOutsideSceneArt,
  orderedSceneChunks, sceneDescription, sceneFloorLinks,
  selectEnvironmentEffects,
} from "./outside-scene";

export interface OutsideSceneProps {
  /** The parsed view; nothing is drawn unless it is `ready`. */
  view: OutsideWorldView;
  /** The zone the semantic panel selected, so both surfaces agree. */
  selectedZoneId?: string | null;
  reducedMotion?: boolean;
  /** Explicit art (tests/host). When omitted, the active theme is probed. */
  art?: OutsideSceneArt | null;
  /** Explicit theme asset base; otherwise read from the active theme attr. */
  assetBase?: string | null;
  fetchImpl?: typeof fetch;
  loadImage?: Parameters<typeof loadOutsideSceneArt>[0]["loadImage"];
  onSelectZone?: (zone: OutsideZone) => void;
  onSelectFloor?: (link: OutsideFloorLink) => void;
}

type ReactArg = OfficeGameShellProps["React"];
type HooksArg = OfficeGameShellProps["hooks"];

const THEME_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

function hostWindow(): Window | null {
  return typeof window === "undefined" ? null : window;
}

function activeThemeId(win: Window | null): string {
  const global = (win as Window & {
    __QUBIT_OFFICE_THEME__?: { id?: unknown };
  } | null)?.__QUBIT_OFFICE_THEME__?.id;
  if (typeof global === "string" && THEME_ID_RE.test(global)) return global;
  const doc = win?.document;
  const raw = doc?.documentElement?.getAttribute("data-office-theme") || "";
  return THEME_ID_RE.test(raw) ? raw : "";
}

/** The same host path the theme payload uses (`/plugins/qubit-office/themes/<id>`). */
export function activeThemeAssetBase(win?: Window | null): string | null {
  const w = win === undefined ? hostWindow() : win;
  const id = activeThemeId(w);
  if (!id) return null;
  return `${hostBasePath()}/plugins/qubit-office/themes/${encodeURIComponent(id)}`;
}

function injectedArtManifest(win: Window | null): unknown {
  if (!win) return undefined;
  return (win as Window & { __QUBIT_OFFICE_OUTSIDE_ART__?: unknown })
    .__QUBIT_OFFICE_OUTSIDE_ART__;
}

/**
 * jsdom ships a `getContext` that only logs "Not implemented" and returns
 * null. Detect it before calling so the accessible fallback is stated once
 * instead of emitting a console error on every mount. A real browser's native
 * implementation (or an explicit test stub) is accepted.
 */
function canvas2dSupported(canvas: HTMLCanvasElement): boolean {
  const view = canvas.ownerDocument?.defaultView;
  const proto = view?.HTMLCanvasElement?.prototype
    ?? (typeof HTMLCanvasElement === "undefined"
      ? null : HTMLCanvasElement.prototype);
  const get = proto?.getContext;
  if (typeof get !== "function") return false;
  const source = String(get);
  // jsdom's wrapper logs "Not implemented" inside the implementation object;
  // it is identifiable without calling it. A test stub replaces the prototype
  // method, so it does not carry this marker and is treated as supported.
  if (/tryWrapperForImpl/.test(source)) return false;
  return !/\bnot implemented\b/i.test(source);
}

function artStatusLabel(
  status: "loading" | "ready" | "absent" | "error",
  art: OutsideSceneArt | null, reason: string,
  effects: readonly string[] = [],
): string {
  if (status === "ready" && art) {
    const credit = art.credit || "https://limezu.itch.io";
    if (art.composed) {
      const roles = Object.keys(art.frames ?? {}).length;
      const ambient = effects.length
        ? ` · ambient ${effects.join(", ")}` : "";
      return `Composed outside scene · ${roles} tile roles${ambient} · `
        + credit;
    }
    return `LimeZu tiles (single-tile fallback) · ${credit}`;
  }
  if (status === "loading") return "Loading local outside art…";
  if (status === "error") {
    return `Procedural scene — local LimeZu art was rejected (${reason}).`;
  }
  return "Procedural scene — local LimeZu pack not detected.";
}

export function createOutsideScene(R: ReactArg, hooks: HooksArg) {
  const { useState, useEffect, useRef } = hooks;

  return function OutsideScene(props: OutsideSceneProps) {
    const world: OutsideWorld | null = props.view.status === "ready"
      ? props.view.world : null;
    const layout = world ? buildOutsideScene(world) : null;
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const [art, setArt] = useState<OutsideSceneArt | null>(
      props.art ?? null);
    const [artStatus, setArtStatus] = useState<
      "loading" | "ready" | "absent" | "error"
    >(props.art ? "ready" : "loading");
    const [artReason, setArtReason] = useState("");
    const [canvasUnavailable, setCanvasUnavailable] = useState(false);

    // Resolve optional real art once per theme/base (or use the explicit prop).
    useEffect(() => {
      if (props.art !== undefined) {
        setArt(props.art);
        setArtStatus(props.art ? "ready" : "absent");
        setArtReason("");
        return;
      }
      const win = hostWindow();
      const injected = injectedArtManifest(win);
      const assetBase = props.assetBase ?? activeThemeAssetBase(win);
      if (!assetBase && injected === undefined) {
        setArt(null);
        setArtStatus("absent");
        setArtReason("");
        return;
      }
      let live = true;
      setArtStatus("loading");
      const origin = win?.location?.origin || "";
      const manifestUrl = assetBase
        ? `${origin}${assetBase}/outside/scene.json` : null;
      const fetchImpl = props.fetchImpl
        ?? (win && typeof win.fetch === "function"
          ? win.fetch.bind(win) : undefined);
      loadOutsideSceneArt({
        manifest: injected,
        manifestUrl,
        assetBase: assetBase ? `${origin}${assetBase}` : null,
        fetchImpl,
        loadImage: props.loadImage,
        source: injected === undefined ? "theme" : "injected",
      }).then((result: OutsideArtLoadResult) => {
        if (!live) return;
        if (result.status === "ready") {
          setArt(result.art);
          setArtStatus("ready");
          setArtReason("");
          return;
        }
        setArt(null);
        setArtStatus(result.status);
        setArtReason(result.reason);
      });
      return () => { live = false; };
    }, [props.art, props.assetBase, props.fetchImpl, props.loadImage]);

    // Paint one deterministic frame per layout/selection/art change. A static
    // frame is always painted first; the animation effect below only starts
    // when the pack carries real animation frames and motion is allowed.
    useEffect(() => {
      if (!layout) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      if (!canvas2dSupported(canvas)) {
        setCanvasUnavailable(true);
        return;
      }
      let ctx: CanvasRenderingContext2D | null = null;
      try {
        ctx = canvas.getContext("2d");
      } catch {
        ctx = null;
      }
      if (!ctx) {
        setCanvasUnavailable(true);
        return;
      }
      setCanvasUnavailable(false);
      canvas.width = Math.max(1, Math.ceil(layout.width * SCENE_TILE_PX));
      canvas.height = Math.max(1, Math.ceil(layout.height * SCENE_TILE_PX));
      drawOutsideScene(ctx, layout, {
        reducedMotion: Boolean(props.reducedMotion),
        art,
        selectedZoneId: props.selectedZoneId ?? null,
      });
    }, [layout?.digest, layout?.width, layout?.height, art, artStatus,
      props.reducedMotion, props.selectedZoneId]);

    // Optional animation: only real pack frames (water waves, doors, ...) and
    // never under reduced motion. The semantic panel remains the live surface.
    useEffect(() => {
      if (!layout || !art?.animated || props.reducedMotion) return;
      const canvas = canvasRef.current;
      if (!canvas || !canvas2dSupported(canvas)) return;
      if (typeof requestAnimationFrame !== "function") return;
      let ctx: CanvasRenderingContext2D | null = null;
      try {
        ctx = canvas.getContext("2d");
      } catch {
        ctx = null;
      }
      if (!ctx) return;
      let raf = 0;
      const start = performance.now();
      const tick = (now: number) => {
        drawOutsideScene(ctx as CanvasRenderingContext2D, layout, {
          timeMs: Math.max(0, now - start),
          reducedMotion: false,
          art,
          selectedZoneId: props.selectedZoneId ?? null,
        });
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => {
        if (typeof cancelAnimationFrame === "function") {
          cancelAnimationFrame(raf);
        }
      };
    }, [layout?.digest, layout?.width, layout?.height, art,
      props.reducedMotion, props.selectedZoneId]);

    if (!world || !layout) return null;

    const effects = selectEnvironmentEffects(layout.environment);
    const effectKinds = effects.map((effect) => effect.kind);
    const environments = currentEnvironment(world);
    const floorLinks = sceneFloorLinks(world, layout);
    const floorByPortal = new Map(
      floorLinks.map((link) => [link.portal_id, link]));

    const onCanvasClick = (event: {
      clientX: number; clientY: number;
    }) => {
      const canvas = canvasRef.current;
      if (!canvas || !props.onSelectZone) return;
      const rect = canvas.getBoundingClientRect();
      if (!(rect.width > 0) || !(rect.height > 0)) return;
      const x = layout.bounds.x
        + ((event.clientX - rect.left) / rect.width) * layout.width;
      const y = layout.bounds.y
        + ((event.clientY - rect.top) / rect.height) * layout.height;
      const hit = [...orderedSceneChunks(layout)].reverse().find((chunk) =>
        chunk.rect.width > 0 && chunk.rect.height > 0
        && x >= chunk.rect.x && x <= chunk.rect.x + chunk.rect.width
        && y >= chunk.rect.y && y <= chunk.rect.y + chunk.rect.height);
      if (!hit) return;
      const zone = world.zones.find((row) => row.zone_id === hit.zone_id);
      if (zone) props.onSelectZone?.(zone);
    };

    const floorButton = (floor: (typeof layout.floors)[number]) => {
      const link = floorByPortal.get(floor.portal_id);
      if (!link) return null;
      const left = ((floor.x - layout.bounds.x) / layout.width) * 100;
      const top = ((floor.y - layout.bounds.y) / layout.height) * 100;
      return R.createElement("a", {
        key: floor.portal_id,
        href: floorHref(floor.floor_id),
        className: "outside-scene__floor",
        "data-testid": "outside-scene-floor",
        "data-floor": floor.floor_id,
        "data-kind": floor.kind,
        "aria-label": link.label,
        style: { left: `${left}%`, top: `${top}%` },
        onClick: (event: { preventDefault?: () => void }) => {
          if (props.onSelectFloor) {
            event.preventDefault?.();
            props.onSelectFloor(link);
          }
        },
      }, `${floor.kind === "stairs" ? "Stairs" : "Elevator"} `
        + `${floor.floor_id}`);
    };

    const envChip = (
      event: { event_id: string; kind: string; value: string; label: string },
    ) => R.createElement("span", {
      key: event.event_id,
      className: "outside-scene__chip",
      "data-testid": "outside-scene-env",
      "data-kind": event.kind,
      "data-value": event.value,
    }, event.label || event.value);

    return R.createElement("section", {
      className: "outside-scene",
      "data-testid": "outside-scene",
      "data-art-status": artStatus,
      "data-art-source": art ? art.source : "procedural",
      "data-art-composed": art?.composed ? "true" : "false",
      "data-art-animated": art?.animated ? "true" : "false",
      "data-effect-kinds": effectKinds.length ? effectKinds.join(",") : "none",
      "data-reduced-motion": props.reducedMotion ? "true" : "false",
      "aria-label": "Visual campus outside world",
    }, [
      R.createElement("div", {
        key: "frame", className: "outside-scene__frame",
      }, [
        R.createElement("canvas", {
          key: "canvas",
          ref: canvasRef,
          className: "outside-scene__canvas",
          role: "img",
          "aria-label": sceneDescription(layout),
          "data-testid": "outside-scene-canvas",
          onClick: onCanvasClick,
        }),
        ...layout.floors.map(floorButton).filter(Boolean),
        R.createElement("div", {
          key: "env", className: "outside-scene__env",
          "aria-label": "Current outside environment",
        }, environments.length
          ? environments.map(envChip)
          : R.createElement("span", {
              key: "none", className: "outside-scene__chip is-unknown",
              "data-testid": "outside-scene-env",
            }, "Environment status unavailable")),
      ]),
      canvasUnavailable
        ? R.createElement("p", {
            key: "unavailable", className: "outside-scene__unavailable",
            "data-testid": "outside-scene-unavailable",
          }, "Visual scene unavailable on this device. The semantic panel below "
            + "still shows every zone and floor link.")
        : null,
      R.createElement("p", {
        key: "caption",
        className: "outside-scene__caption",
        "data-testid": artStatus === "ready"
          ? "outside-scene-art" : "outside-scene-fallback",
      }, artStatusLabel(artStatus, art, artReason, effectKinds)),
      R.createElement("p", {
        key: "counts", className: "outside-scene__counts",
        "data-testid": "outside-scene-counts",
      }, `${layout.summary.active_zones} active of `
        + `${layout.summary.zones} zones · ${layout.chunks.length} visible `
        + `chunks · ${layout.floors.length} floor links. Presentation only — `
        + "no agents or occupancy are drawn."),
    ]);
  };
}
