/* The Qubit campus world: the real office floors arranged as one tower.
 *
 * WHAT THIS REPLACES
 * ------------------
 * The previous campus rendered each floor as an isolated card with its own
 * chrome. The owner rejected that, a floor picker, a text directory and the
 * bare vector shell as the primary surface. This component keeps the same real
 * Habblaud renderer (one iframe per floor), but places the floor scenes on one
 * shared camera stage: a vertical tower with wing offsets, a common zoom/pan,
 * and a label plaque attached to each floor.
 *
 * OCCUPANCY RULES
 * ---------------
 * A live floor's agent count and character names come from the tower payload's
 * own characters, grouped by their real `position.floor`. A template floor has
 * no agents by construction (`agents: 0`, `characters: []`) and is labelled
 * "Unstaffed template". No floor, room or agent is invented, and no live agent
 * is ever moved to another floor.
 *
 * SCOPE
 * -----
 * The full 100-floor tower is not rendered at once: mounting 100 copies of the
 * Habblaud application is not viable. The world loads a bounded window (live
 * floors first, then the template floors nearest them) and offers incremental
 * "load more" controls. Live floors are always in the window.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OfficeAdapter } from "../adapter";
import { fetchDirectory, type FloorTemplate } from "./business-api";
import { officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import { installOfficeBrand } from "./brand-patch";

export interface CampusLiveFloor {
  number: number;
  name: string;
  role: string;
  rooms: number;
  agents: number;
  characters: string[];
}

export interface CampusFloor {
  id: string;
  number: number;
  name: string;
  role: string;
  wing: string;
  live: boolean;
  active: boolean;
  rooms: number;
  desks: number;
  agents: number;
  characters: string[];
  scope: string;
}

export interface CampusWorldProps {
  base?: string;
  theme?: string;
  fetchImpl?: typeof fetch;
  /** Deterministic live floors for tests/hosts; omit to poll the office. */
  liveFloors?: CampusLiveFloor[];
  /** Deterministic templates for tests; omit to read the directory. */
  templates?: FloorTemplate[];
  /** Maximum floor scenes in the current window. */
  maxFloors?: number;
  onSelectFloor?: (floor: CampusFloor) => void;
}

export const CAMPUS_FLOOR_WIDTH = 720;
export const CAMPUS_FLOOR_HEIGHT = 420;
export const CAMPUS_FLOOR_GAP = 88;
export const CAMPUS_DEFAULT_FLOORS = 9;
export const CAMPUS_MIN_ZOOM = 0.22;
export const CAMPUS_MAX_ZOOM = 2.4;

const WINGS = ["north", "east", "south", "west", "central"];
const EMBED_STYLE_ID = "qubit-campus-world-embed";

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function pad3(value: number): string {
  return String(Math.max(0, Math.trunc(value))).padStart(3, "0");
}

function wingIndex(wing: string): number {
  const index = WINGS.indexOf(String(wing || ""));
  return index >= 0 ? index : 2;
}

export function wingTitle(wing: string): string {
  const value = String(wing || "").trim();
  if (!value) return "Campus";
  return `${value.charAt(0).toUpperCase()}${value.slice(1)} Wing`;
}

function finiteNumber(value: unknown): number | null {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function characterName(character: Record<string, unknown>): string {
  const slug = typeof character.slug === "string" ? character.slug.trim() : "";
  if (slug) return slug;
  const id = character.agent_id;
  return id === undefined || id === null ? "" : String(id);
}

/** Project a tower payload into the live floor rows this world can draw. */
export function liveFloorsFromSnapshot(
  payload: unknown,
): CampusLiveFloor[] {
  const root = payload && typeof payload === "object"
    ? payload as Record<string, any> : null;
  const floorplan = root?.floorplan && typeof root.floorplan === "object"
    ? root.floorplan : null;
  const rawFloors = Array.isArray(floorplan?.floors) ? floorplan.floors : [];
  const characters = Array.isArray(root?.characters) ? root.characters : [];
  const byNumber = new Map<number, CampusLiveFloor>();
  for (const floor of rawFloors) {
    const number = finiteNumber(floor?.number);
    if (number === null || number < 0 || byNumber.has(number)) continue;
    const people = characters.filter((character: any) =>
      finiteNumber(character?.position?.floor) === number);
    byNumber.set(number, {
      number,
      name: typeof floor?.name === "string" && floor.name
        ? floor.name : `Floor ${pad3(number)}`,
      role: typeof floor?.role === "string" ? floor.role : "",
      rooms: Array.isArray(floor?.rooms) ? floor.rooms.length : 0,
      agents: people.length,
      characters: people.map((character: any) => characterName(character))
        .filter((name: string) => Boolean(name)),
    });
  }
  return [...byNumber.values()].sort((a, b) => a.number - b.number);
}

function liveFloorRow(floor: CampusLiveFloor): CampusFloor {
  return {
    id: `live:${floor.number}`,
    number: floor.number,
    name: floor.name || `Floor ${pad3(floor.number)}`,
    role: floor.role || "",
    wing: "central",
    live: true,
    active: true,
    rooms: Math.max(0, floor.rooms),
    desks: 0,
    agents: Math.max(0, floor.agents),
    characters: [...floor.characters],
    scope: `live:${floor.number}`,
  };
}

function templateFloorRow(floor: FloorTemplate): CampusFloor {
  return {
    id: floor.floor_id,
    number: floor.index,
    name: floor.name || `Floor ${pad3(floor.index)}`,
    role: wingTitle(floor.wing),
    wing: floor.wing || "central",
    live: false,
    active: floor.active_by_default === true,
    rooms: Math.max(0, floor.room_count),
    desks: Math.max(0, floor.desk_capacity),
    agents: 0,
    characters: [],
    scope: floor.floor_id,
  };
}

function nearestDistance(number: number, anchors: number[]): number {
  let best = Number.POSITIVE_INFINITY;
  for (const anchor of anchors) {
    best = Math.min(best, Math.abs(anchor - number));
  }
  return best;
}

/** Build the bounded, ordered floor window without inventing occupancy. */
export function buildCampusFloors(
  live: CampusLiveFloor[],
  templates: FloorTemplate[] | null | undefined,
  opts: { maxFloors?: number } = {},
): CampusFloor[] {
  const cap = Math.max(1, Math.trunc(opts.maxFloors ?? CAMPUS_DEFAULT_FLOORS));
  const liveByNumber = new Map<number, CampusLiveFloor>();
  for (const row of live ?? []) {
    if (!Number.isFinite(row.number) || row.number < 0) continue;
    if (!liveByNumber.has(row.number)) liveByNumber.set(row.number, row);
  }
  const liveFloors = [...liveByNumber.values()]
    .sort((a, b) => a.number - b.number)
    .map(liveFloorRow);
  const keptLive = liveFloors.slice(0, cap);

  const templateByNumber = new Map<number, FloorTemplate>();
  for (const floor of templates ?? []) {
    if (!Number.isFinite(floor.index) || floor.index <= 0) continue;
    if (liveByNumber.has(floor.index)) continue;
    if (!templateByNumber.has(floor.index)) templateByNumber.set(floor.index, floor);
  }
  const templateFloors = [...templateByNumber.values()]
    .sort((a, b) => a.index - b.index)
    .map(templateFloorRow);

  const anchors = keptLive.length
    ? keptLive.map((floor) => floor.number)
    : (templateFloors.length ? [templateFloors[0].number] : []);
  const remaining = Math.max(0, cap - keptLive.length);
  const selectedTemplates = templateFloors
    .slice()
    .sort((a, b) => {
      const distance = nearestDistance(a.number, anchors)
        - nearestDistance(b.number, anchors);
      return distance !== 0 ? distance : a.number - b.number;
    })
    .slice(0, remaining);

  return [...keptLive, ...selectedTemplates].sort((a, b) => a.number - b.number);
}

export interface CampusPlacement {
  floor: CampusFloor;
  index: number;
  x: number;
  y: number;
}

export interface CampusLayout {
  floors: CampusPlacement[];
  width: number;
  height: number;
}

/** Place the window as one tower: vertical bands with per-wing lateral offsets. */
export function layoutCampusFloors(floors: CampusFloor[]): CampusLayout {
  const placements = floors.map((floor, index) => {
    const wing = wingIndex(floor.wing);
    const x = (wing - 2) * 64 + (index % 2 === 0 ? -18 : 18);
    const y = index * (CAMPUS_FLOOR_HEIGHT + CAMPUS_FLOOR_GAP);
    return { floor, index, x, y };
  });
  const minX = Math.min(0, ...placements.map((item) => item.x));
  const maxX = Math.max(CAMPUS_FLOOR_WIDTH,
    ...placements.map((item) => item.x + CAMPUS_FLOOR_WIDTH));
  const minY = Math.min(0, ...placements.map((item) => item.y));
  const maxY = Math.max(CAMPUS_FLOOR_HEIGHT,
    ...placements.map((item) => item.y + CAMPUS_FLOOR_HEIGHT));
  return {
    floors: placements.map((item) => ({
      ...item, x: item.x - minX, y: item.y - minY,
    })),
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

export interface CampusViewport {
  width: number;
  height: number;
}

export interface CampusCamera {
  x: number;
  y: number;
  zoom: number;
}

/** The shared camera transform that centres one floor in the viewport. */
export function campusCameraFor(
  floor: CampusPlacement | null,
  viewport: CampusViewport,
  zoom: number,
): CampusCamera {
  const z = clamp(Number.isFinite(zoom) ? zoom : 1, CAMPUS_MIN_ZOOM, CAMPUS_MAX_ZOOM);
  const width = Math.max(1, viewport.width);
  const height = Math.max(1, viewport.height);
  const cx = floor ? floor.x + CAMPUS_FLOOR_WIDTH / 2 : CAMPUS_FLOOR_WIDTH / 2;
  const cy = floor ? floor.y + CAMPUS_FLOOR_HEIGHT / 2 : CAMPUS_FLOOR_HEIGHT / 2;
  return { x: width / 2 - cx * z, y: height / 2 - cy * z, zoom: z };
}

/** The next/previous floor in the world order, clamped at the ends. */
export function stepCampusFloor(
  floors: CampusFloor[],
  currentId: string | null,
  delta: number,
): CampusFloor | null {
  if (!floors.length) return null;
  const current = floors.findIndex((floor) => floor.id === currentId);
  if (current < 0) return floors[0];
  const next = clamp(current + (delta || 0), 0, floors.length - 1);
  return floors[next] ?? null;
}

export function campusFloorStatus(floor: CampusFloor): string {
  if (floor.live) {
    return `Live · ${floor.agents} agent${floor.agents === 1 ? "" : "s"}`
      + ` · ${floor.rooms} room${floor.rooms === 1 ? "" : "s"}`;
  }
  return `Unstaffed template · ${floor.rooms} room`
    + `${floor.rooms === 1 ? "" : "s"} · ${floor.desks} desks`;
}

function embedFloorCss(win: Window): void {
  const doc = win?.document;
  if (!doc || !doc.head) return;
  if (doc.getElementById(EMBED_STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = EMBED_STYLE_ID;
  style.textContent = [
    "html,body{margin:0!important;overflow:hidden!important;background:#0b0a13!important}",
    ".ui-topbar,.ui-panel.ui-sidebar,.ui-panel.ui-feed,.ui-lapse-vignette{display:none!important}",
    ".ui-root{padding:0!important}",
  ].join("");
  doc.head.appendChild(style);
}

function installFloorFrame(win: Window): void {
  try {
    installOfficeApiBridge(win);
    installOfficeBrand(win);
    embedFloorCss(win);
  } catch {
    // One failed frame must not take out the other floors.
  }
}

export function createCampusWorld(
  R: OfficeGameShellProps["React"],
  H: OfficeGameShellProps["hooks"],
) {
  return function CampusWorld(props: CampusWorldProps) {
    const { useState, useEffect, useRef } = H;
    const [live, setLive] = useState<CampusLiveFloor[]>(
      props.liveFloors ?? []);
    const [templates, setTemplates] = useState<FloorTemplate[] | null>(
      props.templates ?? null);
    const [liveError, setLiveError] = useState("");
    const [templateError, setTemplateError] = useState("");
    const [limit, setLimit] = useState(
      Math.max(1, props.maxFloors ?? CAMPUS_DEFAULT_FLOORS));
    const [focusId, setFocusId] = useState<string | null>(null);
    const [zoom, setZoom] = useState(0.64);
    const [camera, setCamera] = useState<CampusCamera>(
      { x: 0, y: 0, zoom: 0.64 });
    const [viewport, setViewport] = useState<CampusViewport>(
      { width: 1280, height: 720 });
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const dragRef = useRef<{
      pointerId: number;
      startX: number;
      startY: number;
      originX: number;
      originY: number;
    } | null>(null);

    useEffect(() => {
      if (props.liveFloors !== undefined) {
        setLive(props.liveFloors);
        return;
      }
      let active = true;
      const adapter = new OfficeAdapter({
        baseUrl: props.base ? `${props.base}/agent` : "/agent",
        intervalMs: 4000,
        fetchImpl: props.fetchImpl,
        onSnapshot: (snapshot) => {
          if (!active) return;
          if (!snapshot.payload) {
            if (snapshot.error) setLiveError(snapshot.error);
            return;
          }
          setLive(liveFloorsFromSnapshot(snapshot.payload));
          setLiveError(snapshot.error ?? "");
        },
      });
      adapter.start();
      return () => { active = false; adapter.dispose(); };
    }, [props.liveFloors, props.base, props.fetchImpl]);

    useEffect(() => {
      if (props.templates !== undefined) {
        setTemplates(props.templates);
        return;
      }
      if (props.liveFloors !== undefined) {
        setTemplates([]);
        return;
      }
      let active = true;
      const ctl = typeof AbortController === "undefined"
        ? undefined : new AbortController();
      setTemplateError("");
      fetchDirectory({
        base: props.base, fetchImpl: props.fetchImpl, signal: ctl?.signal,
      })
        .then((directory) => {
          if (active) setTemplates(Array.isArray(directory.floors)
            ? directory.floors : []);
        })
        .catch((exc) => {
          if (!active) return;
          setTemplates([]);
          setTemplateError(exc instanceof Error ? exc.message : String(exc));
        });
      return () => { active = false; ctl?.abort(); };
    }, [props.templates, props.liveFloors, props.base, props.fetchImpl]);

    useEffect(() => {
      const node = viewportRef.current;
      if (!node) return;
      const measure = () => {
        const rect = node.getBoundingClientRect?.();
        if (rect && rect.width > 1 && rect.height > 1) {
          setViewport({ width: rect.width, height: rect.height });
        }
      };
      measure();
      let observer: ResizeObserver | null = null;
      if (typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(measure);
        observer.observe(node);
      } else if (typeof window !== "undefined") {
        window.addEventListener("resize", measure);
      }
      return () => {
        observer?.disconnect();
        if (typeof window !== "undefined") {
          window.removeEventListener("resize", measure);
        }
      };
    }, []);

    const floors = buildCampusFloors(live, templates, { maxFloors: limit });
    const layout = layoutCampusFloors(floors);
    const floorKey = floors.map((floor) => floor.id).join("|");
    const layoutKey = layout.floors.map(
      (item) => `${item.floor.id}:${item.x}:${item.y}`).join("|");
    const knownNumbers = new Set<number>();
    for (const floor of live) {
      if (Number.isFinite(floor.number) && floor.number >= 0) {
        knownNumbers.add(floor.number);
      }
    }
    for (const floor of templates ?? []) {
      if (Number.isFinite(floor.index) && floor.index > 0) {
        knownNumbers.add(floor.index);
      }
    }
    const totalFloors = knownNumbers.size;
    const liveCount = floors.filter((floor) => floor.live).length;

    useEffect(() => {
      if (!floors.length) {
        if (focusId !== null) setFocusId(null);
        return;
      }
      if (focusId && floors.some((floor) => floor.id === focusId)) return;
      const first = floors.find((floor) => floor.live) ?? floors[0];
      setFocusId(first.id);
    }, [floorKey, focusId]);

    const focused = focusId
      ? layout.floors.find((item) => item.floor.id === focusId) ?? null
      : null;

    useEffect(() => {
      if (!layout.floors.length) return;
      const node = viewportRef.current;
      let width = viewport.width;
      let height = viewport.height;
      const rect = node?.getBoundingClientRect?.();
      if (rect && rect.width > 1 && rect.height > 1) {
        width = rect.width;
        height = rect.height;
      }
      setCamera(campusCameraFor(focused, { width, height }, zoom));
    }, [focusId, layoutKey, zoom, viewport.width, viewport.height]);

    const selectFloor = (floor: CampusFloor) => {
      setFocusId(floor.id);
      setZoom((value: number) => Math.max(value, 0.9));
      props.onSelectFloor?.(floor);
    };

    const step = (delta: number) => {
      const next = stepCampusFloor(floors, focusId, delta);
      if (next) selectFloor(next);
    };

    const setZoomBy = (factor: number) => {
      setZoom((value: number) => clamp(
        value * factor, CAMPUS_MIN_ZOOM, CAMPUS_MAX_ZOOM));
    };

    const resetView = () => {
      const first = floors.find((floor) => floor.id === focusId)
        ?? floors.find((floor) => floor.live) ?? floors[0];
      if (first) setFocusId(first.id);
      setZoom(0.64);
    };

    const onPointerDown = (event: {
      button?: number; clientX: number; clientY: number; pointerId: number;
      target?: { closest?: (selector: string) => unknown } | null;
      currentTarget?: { setPointerCapture?: (id: number) => void } | null;
    }) => {
      if (event.button !== undefined && event.button !== 0) return;
      if (event.target?.closest?.("button")) return;
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX, startY: event.clientY,
        originX: camera.x, originY: camera.y,
      };
      event.currentTarget?.setPointerCapture?.(event.pointerId);
    };

    const onPointerMove = (event: {
      clientX: number; clientY: number; pointerId: number;
    }) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      setCamera((value: CampusCamera) => ({
        ...value,
        x: drag.originX + (event.clientX - drag.startX),
        y: drag.originY + (event.clientY - drag.startY),
      }));
    };

    const endDrag = (event: {
      pointerId: number;
      currentTarget?: { releasePointerCapture?: (id: number) => void } | null;
    }) => {
      if (!dragRef.current) return;
      dragRef.current = null;
      event.currentTarget?.releasePointerCapture?.(event.pointerId);
    };

    const onKeyDown = (event: {
      key: string; preventDefault?: () => void;
    }) => {
      if (event.key === "ArrowDown") {
        event.preventDefault?.();
        step(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault?.();
        step(-1);
      } else if (event.key === "+" || event.key === "=") {
        event.preventDefault?.();
        setZoomBy(1.2);
      } else if (event.key === "-") {
        event.preventDefault?.();
        setZoomBy(1 / 1.2);
      } else if (event.key === "0") {
        event.preventDefault?.();
        resetView();
      }
    };

    const canLoadMore = floors.length < totalFloors;
    const canLoadFewer = floors.length > 4 && limit > 4;

    const controlButton = (key: string, label: string,
                           testId: string, onClick: () => void,
                           disabled = false) => R.createElement("button", {
      key, type: "button", className: "office-game__btn",
      "data-testid": testId,
      disabled,
      onClick,
    }, label);

    const worldState = () => {
      if (!floors.length) {
        const loading = (props.liveFloors === undefined && !liveError)
          || (props.templates === undefined && templates === null);
        if (loading) {
          return R.createElement("p", {
            key: "state", className: "office-game__campusWorldState",
            role: "status", "data-testid": "campus-world-state",
          }, "Loading the campus floors…");
        }
        return R.createElement("p", {
          key: "state", className: "office-game__campusWorldState",
          role: "alert", "data-testid": "campus-world-state",
        }, "The campus could not read live floors or floor templates. "
          + "No floors or agents are shown rather than inventing them.");
      }
      const notes: string[] = [];
      if (liveError) {
        notes.push(`Live occupancy unavailable (${liveError}). `
          + "Showing the real unstaffed floor templates.");
      }
      if (templateError) {
        notes.push(`Floor templates unavailable (${templateError}).`);
      }
      if (!notes.length) return null;
      return R.createElement("p", {
        key: "state", className: "office-game__campusWorldState",
        role: "status", "data-testid": "campus-world-state",
      }, notes.join(" "));
    };

    const floorSlab = (item: CampusPlacement) => {
      const floor = item.floor;
      const isFocus = focusId === floor.id;
      const className = "office-game__campusFloor"
        + (floor.live ? " is-live" : " is-template")
        + (isFocus ? " is-focus" : "");
      return R.createElement("section", {
        key: floor.id,
        className,
        "data-testid": "campus-world-floor",
        "data-floor": floor.id,
        "data-floor-number": floor.number,
        "data-live": floor.live ? "true" : "false",
        "data-agents": String(floor.agents),
        "data-focus": isFocus ? "true" : "false",
        "aria-label": `${floor.live ? "Live" : "Unstaffed"} floor `
          + `${floor.number}, ${floor.name}, ${floor.rooms} rooms, `
          + `${floor.agents} agents`,
        style: {
          left: `${item.x}px`, top: `${item.y}px`,
          width: `${CAMPUS_FLOOR_WIDTH}px`, height: `${CAMPUS_FLOOR_HEIGHT}px`,
        },
      }, [
        R.createElement("button", {
          key: "label", type: "button",
          className: "office-game__campusFloorLabel",
          "data-testid": "campus-world-floor-label",
          "aria-pressed": isFocus ? "true" : "false",
          "aria-current": isFocus ? "true" : undefined,
          "aria-label": `Enter floor ${floor.number}, ${floor.name}`,
          onClick: () => selectFloor(floor),
        }, [
          R.createElement("span", {
            key: "n", className: "office-game__campusFloorNumber",
          }, `Floor ${pad3(floor.number)}`),
          R.createElement("span", {
            key: "name", className: "office-game__campusFloorName",
          }, floor.name),
          R.createElement("span", {
            key: "s", className: "office-game__campusFloorState",
          }, campusFloorStatus(floor)),
        ]),
        R.createElement("iframe", {
          key: "frame",
          title: `Floor ${pad3(floor.number)} · ${floor.name}`,
          "data-testid": "campus-world-frame",
          "data-floor-scope": floor.scope,
          className: "office-game__campusFloorFrame",
          sandbox: "allow-scripts allow-same-origin allow-forms "
            + "allow-downloads allow-popups allow-modals",
          allow: "clipboard-read; clipboard-write",
          loading: floor.live || isFocus ? "eager" : "lazy",
          src: officeAppSrc({
            base: props.base, theme: props.theme, floor: floor.scope,
          }),
          onLoad: (event: { currentTarget: HTMLIFrameElement }) => {
            const win = event.currentTarget.contentWindow as Window | null;
            if (win) installFloorFrame(win);
          },
        }),
      ]);
    };

    const focusBar = focused
      ? R.createElement("div", {
          key: "focus", className: "office-game__campusFocusBar",
          "data-testid": "campus-world-focus", role: "status",
        }, [
          R.createElement("strong", {
            key: "title", className: "office-game__campusFocusTitle",
            "data-testid": "campus-world-focus-title",
          }, `Floor ${pad3(focused.floor.number)} · ${focused.floor.name}`),
          R.createElement("span", {
            key: "meta", className: "office-game__campusFocusMeta",
            "data-testid": "campus-world-focus-meta",
          }, campusFloorStatus(focused.floor)),
          focused.floor.characters.length
            ? R.createElement("span", {
                key: "people", className: "office-game__campusFocusPeople",
                title: focused.floor.characters.join(", "),
                "data-testid": "campus-world-focus-people",
              }, focused.floor.characters.join(", "))
            : null,
          R.createElement("div", {
            key: "actions", className: "office-game__campusFocusActions",
          }, [
            controlButton("prev", "Floor above", "campus-world-prev",
              () => step(-1), floors.indexOf(focused.floor) <= 0),
            controlButton("next", "Floor below", "campus-world-next",
              () => step(1),
              floors.indexOf(focused.floor) >= floors.length - 1),
            controlButton("reset", "Reset view", "campus-world-reset",
              resetView),
          ]),
        ])
      : null;

    return R.createElement("div", {
      className: "office-game__campusWorld",
      "data-testid": "office-campus-world",
      "data-floor-count": String(floors.length),
      "data-live-floors": String(liveCount),
    }, [
      R.createElement("div", {
        key: "controls", className: "office-game__campusWorldControls",
        role: "toolbar", "aria-label": "Campus world controls",
      }, [
        R.createElement("div", {
          key: "meta", className: "office-game__campusWorldMeta",
        }, [
          R.createElement("strong", { key: "title" }, "Campus world"),
          R.createElement("span", {
            key: "counts", "data-testid": "campus-world-counts",
          }, `${floors.length} of ${totalFloors} floors in view · `
            + `${liveCount} live · one shared camera`),
        ]),
        controlButton("zoom-in", "Zoom in", "campus-world-zoom-in",
          () => setZoomBy(1.2)),
        controlButton("zoom-out", "Zoom out", "campus-world-zoom-out",
          () => setZoomBy(1 / 1.2)),
        controlButton("more", "Load more floors", "campus-world-more",
          () => setLimit((value: number) => value + 6), !canLoadMore),
        controlButton("fewer", "Load fewer floors", "campus-world-fewer",
          () => setLimit((value: number) => Math.max(4, value - 6)),
          !canLoadFewer),
      ]),
      worldState(),
      R.createElement("div", {
        key: "viewport",
        ref: viewportRef,
        className: "office-game__campusViewport",
        "data-testid": "campus-world-viewport",
        role: "region",
        "aria-label": "Campus world floors",
        tabIndex: 0,
        onKeyDown,
        onPointerDown,
        onPointerMove,
        onPointerUp: endDrag,
        onPointerCancel: endDrag,
        onPointerLeave: endDrag,
      }, [
        R.createElement("div", {
          key: "stage",
          className: "office-game__campusStage",
          "data-testid": "campus-world-stage",
          style: {
            width: `${layout.width}px`,
            height: `${layout.height}px`,
            transform: `translate3d(${camera.x}px, ${camera.y}px, 0) `
              + `scale(${camera.zoom})`,
          },
        }, layout.floors.map(floorSlab)),
        focusBar,
      ]),
    ]);
  };
}
