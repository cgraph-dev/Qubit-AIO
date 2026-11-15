/* The Qubit campus world: one room-scale floor at a time.
 *
 * WHAT THIS REPLACES
 * ------------------
 * The previous campus placed every floor on one stage as a miniature tower:
 * one iframe per floor, all shrunk under a shared camera. The owner rejected
 * the stack of tiny previews. This component keeps the real Habblaud renderer
 * but mounts exactly ONE floor scene at a time, at room scale, and gives the
 * reader a building elevator plus enterable rooms instead of a wall of
 * thumbnails.
 *
 * WHAT IS REAL
 * ------------
 * Floors, rooms, room names, capacity and occupancy come from the payloads the
 * office already serves:
 *   - the live tower snapshot (`/agent/api/office/tower`) supplies the real
 *     characters grouped by their real `position.floor` and `position.room_id`;
 *   - the world tower (`/agent/api/office/world/tower`) supplies the shipped
 *     room plan for every floor, including the 100 unstaffed templates.
 * Nothing is invented: a template floor has zero agents by construction and is
 * labelled "Unstaffed"; a live character without a declared room is shown on
 * the floor but never assigned to a room.
 *
 * THE ONE RENDERER
 * ----------------
 * Only the selected floor's iframe is mounted. Changing floors swaps that
 * single iframe's `?floor=live:<n>` / `?floor=floor-XXX` scope, so the real
 * renderer draws one large room scene rather than 9 miniature copies.
 *
 * SECONDARY SURFACE
 * -----------------
 * The 100-floor directory remains available from the shell's Campus tab. This
 * component is the primary world; it never renders a text directory as its
 * default view.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OfficeAdapter } from "../adapter";
import { fetchDirectory, type FloorTemplate } from "./business-api";
import { officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import { installOfficeBrand } from "./brand-patch";
import {
  fetchWorldTower, type WorldFloor, type WorldTower,
} from "./world-tower";

/** One declared room on a floor. Occupants are real names or empty. */
export interface CampusRoom {
  id: string;
  name: string;
  kind: string;
  /** True when the room is a shipped plan room with no live occupant. */
  template: boolean;
  active: boolean;
  capacity: number;
  agents: string[];
}

export interface CampusLiveFloor {
  number: number;
  name: string;
  role: string;
  rooms: number;
  agents: number;
  characters: string[];
  roomDetails?: CampusRoom[];
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
  roomDetails: CampusRoom[];
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
  /** Deterministic world/room plan for tests; omit to read the endpoint. */
  world?: WorldTower | null;
  /** Optional cap for the elevator list; omitted means every known floor. */
  maxFloors?: number;
  /** Start on one floor (`live:1` or `floor-003`) instead of the first live. */
  initialFloor?: string | null;
  onSelectFloor?: (floor: CampusFloor) => void;
  /** Open one floor in the full office view. */
  onOpenFloor?: (floor: CampusFloor) => void;
}

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

function characterRoomId(character: Record<string, unknown>): number | null {
  const position = character.position;
  if (!position || typeof position !== "object") return null;
  return finiteNumber((position as Record<string, unknown>).room_id);
}

function roomName(raw: Record<string, unknown>, index: number): string {
  const slug = typeof raw.slug === "string" ? raw.slug.trim() : "";
  if (slug) return slug;
  const kind = typeof raw.kind === "string" ? raw.kind.trim() : "";
  if (kind) return kind;
  return `Room ${index + 1}`;
}

/** Real rooms from one live floorplan floor, with real occupants attached. */
function liveRooms(
  number: number,
  floor: Record<string, any>,
  people: Array<Record<string, any>>,
): CampusRoom[] {
  const rawRooms = Array.isArray(floor?.rooms) ? floor.rooms : [];
  const seen = new Set<string>();
  const rooms: CampusRoom[] = [];
  rawRooms.forEach((raw: any, index: number) => {
    if (!raw || typeof raw !== "object") return;
    const roomNumber = finiteNumber(raw.id);
    const id = `f${number}r${roomNumber === null ? index : roomNumber}`;
    if (seen.has(id)) return;
    seen.add(id);
    const agents = people
      .filter((character) => characterRoomId(character) === roomNumber)
      .map(characterName)
      .filter((name) => Boolean(name));
    rooms.push({
      id,
      name: roomName(raw, index),
      kind: typeof raw.kind === "string" ? raw.kind : "",
      template: false,
      active: true,
      capacity: Array.isArray(raw.seats) ? raw.seats.length : 0,
      agents,
    });
  });
  return rooms;
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
    const roomDetails = liveRooms(number, floor, people);
    byNumber.set(number, {
      number,
      name: typeof floor?.name === "string" && floor.name
        ? floor.name : `Floor ${pad3(number)}`,
      role: typeof floor?.role === "string" ? floor.role : "",
      rooms: roomDetails.length
        || (Array.isArray(floor?.rooms) ? floor.rooms.length : 0),
      agents: people.length,
      characters: people.map((character: any) => characterName(character))
        .filter((name: string) => Boolean(name)),
      roomDetails,
    });
  }
  return [...byNumber.values()].sort((a, b) => a.number - b.number);
}

function liveFloorRow(floor: CampusLiveFloor): CampusFloor {
  const roomDetails = (floor.roomDetails ?? []).map((room) => ({
    ...room, agents: [...room.agents],
  }));
  return {
    id: `live:${floor.number}`,
    number: floor.number,
    name: floor.name || `Floor ${pad3(floor.number)}`,
    role: floor.role || "",
    wing: "central",
    live: true,
    active: true,
    rooms: Math.max(roomDetails.length, floor.rooms),
    desks: roomDetails.reduce((sum, room) => sum + room.capacity, 0),
    agents: Math.max(0, floor.agents),
    characters: [...floor.characters],
    roomDetails,
    scope: `live:${floor.number}`,
  };
}

/** Rooms declared for a template floor by the directory and/or world plan. */
export function templateRoomsFor(
  floor: FloorTemplate,
  worldFloor?: WorldFloor | null,
): CampusRoom[] {
  const planned = worldFloor?.rooms ?? [];
  if (planned.length) {
    return planned.map((room) => ({
      id: room.room_id,
      name: room.name || room.room_id,
      kind: room.kind || "",
      template: true,
      active: room.active,
      capacity: Math.max(0, room.capacity),
      agents: [],
    }));
  }
  const count = Math.max(0, Math.trunc(floor.room_count));
  const desks = Math.max(0, Math.trunc(floor.desk_capacity));
  const base = count > 0 ? Math.floor(desks / count) : 0;
  const extra = count > 0 ? desks % count : 0;
  return Array.from({ length: count }, (_, index) => ({
    id: `${floor.floor_id}-room-${index + 1}`,
    name: `Room ${index + 1}`,
    kind: "template",
    template: true,
    active: floor.active_by_default,
    capacity: base + (index < extra ? 1 : 0),
    agents: [],
  }));
}

function templateFloorRow(
  floor: FloorTemplate,
  worldFloor?: WorldFloor | null,
): CampusFloor {
  const roomDetails = templateRoomsFor(floor, worldFloor);
  return {
    id: floor.floor_id,
    number: floor.index,
    name: floor.name || `Floor ${pad3(floor.index)}`,
    role: wingTitle(floor.wing),
    wing: floor.wing || "central",
    live: false,
    active: floor.active_by_default === true,
    rooms: roomDetails.length || Math.max(0, floor.room_count),
    desks: Math.max(0, floor.desk_capacity),
    agents: 0,
    characters: [],
    roomDetails,
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

/** Build the ordered floor list without inventing occupancy.
 *
 * The world plan (when available) supplies the real room names/capacity for
 * template floors. Without it the directory's declared room count is used and
 * every room is still explicitly marked as a template.
 */
export function buildCampusFloors(
  live: CampusLiveFloor[],
  templates: FloorTemplate[] | null | undefined,
  opts: { maxFloors?: number; world?: WorldTower | null } = {},
): CampusFloor[] {
  const cap = opts.maxFloors === undefined
    ? Number.POSITIVE_INFINITY
    : Math.max(1, Math.trunc(opts.maxFloors));
  const worldByIndex = new Map<number, WorldFloor>();
  const worldById = new Map<string, WorldFloor>();
  for (const floor of opts.world?.floors ?? []) {
    worldByIndex.set(floor.index, floor);
    worldById.set(floor.floor_id, floor);
  }
  const liveByNumber = new Map<number, CampusLiveFloor>();
  for (const row of live ?? []) {
    if (!Number.isFinite(row.number) || row.number < 0) continue;
    if (!liveByNumber.has(row.number)) liveByNumber.set(row.number, row);
  }
  const liveFloors = [...liveByNumber.values()]
    .sort((a, b) => a.number - b.number)
    .map(liveFloorRow);
  // Enrich a live floor with the plan's real room names only when the live
  // snapshot did not declare the rooms itself. These stay template-labelled
  // because no live occupant was placed in them.
  for (const floor of liveFloors) {
    if (floor.roomDetails.length) continue;
    const plan = worldByIndex.get(floor.number);
    if (!plan) continue;
    floor.roomDetails = plan.rooms.map((room) => ({
      id: room.room_id,
      name: room.name || room.room_id,
      kind: room.kind || "",
      template: true,
      active: room.active,
      capacity: Math.max(0, room.capacity),
      agents: [],
    }));
    floor.rooms = floor.roomDetails.length;
  }
  const keptLive = Number.isFinite(cap) ? liveFloors.slice(0, cap) : liveFloors;

  const templateByNumber = new Map<number, FloorTemplate>();
  for (const floor of templates ?? []) {
    if (!Number.isFinite(floor.index) || floor.index <= 0) continue;
    if (liveByNumber.has(floor.index)) continue;
    if (!templateByNumber.has(floor.index)) templateByNumber.set(floor.index, floor);
  }
  const templateFloors = [...templateByNumber.values()]
    .sort((a, b) => a.index - b.index)
    .map((floor) => templateFloorRow(
      floor,
      worldById.get(floor.floor_id) ?? worldByIndex.get(floor.index),
    ));

  const room = Number.isFinite(cap) ? Math.max(0, cap - keptLive.length) : Infinity;
  const anchors = keptLive.length
    ? keptLive.map((floor) => floor.number)
    : (templateFloors.length ? [templateFloors[0].number] : []);
  const selectedTemplates = Number.isFinite(room)
    ? templateFloors
      .slice()
      .sort((a, b) => {
        const distance = nearestDistance(a.number, anchors)
          - nearestDistance(b.number, anchors);
        return distance !== 0 ? distance : a.number - b.number;
      })
      .slice(0, room)
    : templateFloors;

  return [...keptLive, ...selectedTemplates].sort((a, b) => a.number - b.number);
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

/** The next/previous room in the selected floor, wrapping the ends. */
export function stepCampusRoom(
  rooms: CampusRoom[],
  currentId: string | null,
  delta: number,
): CampusRoom | null {
  if (!rooms.length) return null;
  const current = rooms.findIndex((room) => room.id === currentId);
  if (current < 0) return rooms[delta < 0 ? rooms.length - 1 : 0] ?? null;
  const next = clamp(current + (delta || 0), 0, rooms.length - 1);
  return rooms[next] ?? null;
}

export function campusFloorStatus(floor: CampusFloor): string {
  if (floor.live) {
    return `Live · ${floor.agents} agent${floor.agents === 1 ? "" : "s"}`
      + ` · ${floor.rooms} room${floor.rooms === 1 ? "" : "s"}`;
  }
  return `Unstaffed template · ${floor.rooms} room`
    + `${floor.rooms === 1 ? "" : "s"} · ${floor.desks} desks`;
}

export function campusRoomStatus(room: CampusRoom): string {
  if (room.template) {
    return `Unstaffed template · capacity ${room.capacity}`;
  }
  return `${room.agents.length} agent${room.agents.length === 1 ? "" : "s"}`
    + ` · capacity ${room.capacity}`;
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
    // One failed frame must not take out the world.
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
    const [world, setWorld] = useState<WorldTower | null>(
      props.world === undefined ? null : props.world);
    const [liveError, setLiveError] = useState("");
    const [templateError, setTemplateError] = useState("");
    const [focusId, setFocusId] = useState<string | null>(
      props.initialFloor ?? null);
    const [roomFocusId, setRoomFocusId] = useState<string | null>(null);
    const [characterFocus, setCharacterFocus] = useState<string | null>(null);
    const elevatorRef = useRef<HTMLDivElement | null>(null);

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
      if (props.world !== undefined) {
        setWorld(props.world);
        return;
      }
      if (props.liveFloors !== undefined) {
        setWorld(null);
        return;
      }
      const floorRows = props.templates ?? templates;
      if (!floorRows || !floorRows.length) return;
      let active = true;
      const ctl = typeof AbortController === "undefined"
        ? undefined : new AbortController();
      fetchWorldTower(floorRows, {
        base: props.base, fetchImpl: props.fetchImpl, signal: ctl?.signal,
      })
        .then((tower) => { if (active) setWorld(tower); })
        .catch(() => { if (active) setWorld(null); });
      return () => { active = false; ctl?.abort(); };
    }, [props.world, props.liveFloors, props.templates, templates,
        props.base, props.fetchImpl]);

    const floors = buildCampusFloors(live, templates, {
      maxFloors: props.maxFloors, world,
    });
    const floorKey = floors.map((floor) => floor.id).join("|");
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

    useEffect(() => {
      // Entering another floor leaves the previous floor's room behind: the
      // room id belongs to one floor's plan and must not be carried across.
      setRoomFocusId(null);
      setCharacterFocus(null);
    }, [focusId]);

    useEffect(() => {
      const node = elevatorRef.current;
      const target = node?.querySelector?.(
        '[aria-current="true"]') as HTMLElement | null;
      target?.scrollIntoView?.({ block: "nearest" });
    }, [focusId]);

    const focused = focusId
      ? floors.find((floor) => floor.id === focusId) ?? null
      : null;
    const rooms = focused?.roomDetails ?? [];
    const focusedRoom = roomFocusId
      ? rooms.find((room) => room.id === roomFocusId) ?? null
      : null;

    const selectFloor = (floor: CampusFloor) => {
      setFocusId(floor.id);
      props.onSelectFloor?.(floor);
    };

    const selectIndex = (index: number) => {
      if (!floors.length) return;
      const floor = floors[clamp(index, 0, floors.length - 1)];
      if (floor) selectFloor(floor);
    };

    const stepFloor = (delta: number) => {
      const next = stepCampusFloor(floors, focusId, delta);
      if (next) selectFloor(next);
    };

    const enterRoom = (room: CampusRoom, character?: string | null) => {
      setRoomFocusId(room.id);
      setCharacterFocus(character ?? null);
    };

    const focusCharacter = (name: string) => {
      const room = rooms.find((candidate) => candidate.agents.includes(name));
      if (room) {
        enterRoom(room, name);
        return;
      }
      // An unplaced character is still on the real floor; never move them into
      // a room just to make the panel look complete.
      setRoomFocusId(null);
      setCharacterFocus(name);
    };

    const exitRoom = () => {
      setRoomFocusId(null);
      setCharacterFocus(null);
    };

    const moveRoom = (delta: number) => {
      const next = stepCampusRoom(rooms, roomFocusId, delta);
      if (next) enterRoom(next);
    };

    const onKeyDown = (event: {
      key: string; preventDefault?: () => void;
    }) => {
      switch (event.key) {
        case "ArrowUp": stepFloor(-1); break;
        case "ArrowDown": stepFloor(1); break;
        case "PageUp": stepFloor(-10); break;
        case "PageDown": stepFloor(10); break;
        case "Home": selectIndex(0); break;
        case "End": selectIndex(floors.length - 1); break;
        case "ArrowLeft": moveRoom(-1); break;
        case "ArrowRight": moveRoom(1); break;
        case "Escape":
          if (roomFocusId || characterFocus) {
            exitRoom();
            break;
          }
          return;
        default:
          return;
      }
      event.preventDefault?.();
    };

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

    const floorOption = (floor: CampusFloor) => {
      const isFocus = focusId === floor.id;
      return R.createElement("button", {
        key: floor.id,
        type: "button",
        className: "office-game__campusElevatorFloor"
          + (floor.live ? " is-live" : " is-template")
          + (isFocus ? " is-focus" : ""),
        "data-testid": "campus-world-floor-option",
        "data-floor-id": floor.id,
        "data-floor-number": floor.number,
        "data-live": floor.live ? "true" : "false",
        "data-agents": String(floor.agents),
        "aria-current": isFocus ? "true" : undefined,
        "aria-label": `Floor ${pad3(floor.number)}, ${floor.name}, `
          + `${campusFloorStatus(floor)}${isFocus ? ". Current floor" : ""}`,
        onClick: () => selectFloor(floor),
      }, [
        R.createElement("span", {
          key: "number", className: "office-game__campusElevatorNumber",
        }, pad3(floor.number)),
        R.createElement("span", {
          key: "name", className: "office-game__campusElevatorName",
        }, floor.name),
        R.createElement("span", {
          key: "badge", className: "office-game__campusElevatorBadge",
        }, floor.live ? `Live · ${floor.agents}` : "Unstaffed"),
      ]);
    };

    const roomChip = (room: CampusRoom) => {
      const isFocus = roomFocusId === room.id;
      return R.createElement("button", {
        key: room.id,
        type: "button",
        className: "office-game__campusRoomChip"
          + (room.template ? " is-template" : " is-live")
          + (isFocus ? " is-focus" : ""),
        "data-testid": "campus-world-room",
        "data-room-id": room.id,
        "data-template": room.template ? "true" : "false",
        "aria-pressed": isFocus ? "true" : "false",
        "aria-label": `${room.template ? "Unstaffed template room"
          : "Enter room"} ${room.name}, ${campusRoomStatus(room)}`,
        onClick: () => enterRoom(room),
      }, [
        R.createElement("span", {
          key: "name", className: "office-game__campusRoomName",
        }, room.name),
        R.createElement("span", {
          key: "meta", className: "office-game__campusRoomMeta",
        }, room.template ? "Unstaffed"
          : `${room.agents.length} agent${room.agents.length === 1 ? "" : "s"}`),
      ]);
    };

    const characterChip = (name: string) => R.createElement("button", {
      key: name,
      type: "button",
      className: "office-game__campusCharacter"
        + (characterFocus === name ? " is-focus" : ""),
      "data-testid": "campus-world-character",
      "data-character": name,
      "aria-pressed": characterFocus === name ? "true" : "false",
      "aria-label": `Focus ${name} on this floor`,
      onClick: () => focusCharacter(name),
    }, name);

    const roomPanel = focusedRoom
      ? R.createElement("section", {
          key: "room-focus",
          className: "office-game__campusRoomFocus"
            + (focusedRoom.template ? " is-template" : " is-live"),
          "data-testid": "campus-world-room-focus",
          "data-room-id": focusedRoom.id,
          "data-template": focusedRoom.template ? "true" : "false",
          "aria-label": `Room ${focusedRoom.name}`,
        }, [
          R.createElement("div", {
            key: "head", className: "office-game__campusRoomFocusHead",
          }, [
            R.createElement("strong", {
              key: "title", "data-testid": "campus-world-room-focus-title",
            }, focusedRoom.name),
            R.createElement("span", {
              key: "status", className: "office-game__campusRoomFocusStatus",
              "data-testid": "campus-world-room-focus-status",
            }, campusRoomStatus(focusedRoom)),
          ]),
          focusedRoom.template
            ? R.createElement("p", {
                key: "empty", className: "office-game__campusRoomFocusNote",
                "data-testid": "campus-world-room-empty",
              }, "Unstaffed template room — no agent is placed here.")
            : focusedRoom.agents.length
            ? R.createElement("div", {
                key: "agents", className: "office-game__campusRoomAgents",
              }, focusedRoom.agents.map(characterChip))
            : R.createElement("p", {
                key: "empty", className: "office-game__campusRoomFocusNote",
                "data-testid": "campus-world-room-empty",
              }, "No agent is placed in this room right now."),
          R.createElement("button", {
            key: "exit", type: "button", className: "office-game__btn",
            "data-testid": "campus-world-room-exit",
            onClick: exitRoom,
          }, "Back to floor"),
        ])
      : null;

    const unplaced = characterFocus
      && !focusedRoom ? characterFocus : null;

    const plaque = focused
      ? R.createElement("div", {
          key: "plaque", className: "office-game__campusFloorPlaque",
          "data-testid": "campus-world-plaque",
        }, [
          R.createElement("strong", {
            key: "title", "data-testid": "campus-world-focus-title",
          }, `Floor ${pad3(focused.number)} · ${focused.name}`),
          R.createElement("span", {
            key: "meta", "data-testid": "campus-world-focus-meta",
          }, campusFloorStatus(focused)),
        ])
      : null;

    const floorScene = focused
      ? R.createElement("div", {
          key: "scene",
          className: "office-game__campusFloorScene",
          "data-testid": "campus-world-floor",
          "data-floor": focused.id,
          "data-floor-number": focused.number,
          "data-live": focused.live ? "true" : "false",
          "data-agents": String(focused.agents),
        }, [
          R.createElement("iframe", {
            key: `frame-${focused.id}`,
            title: `Floor ${pad3(focused.number)} · ${focused.name}`,
            "data-testid": "campus-world-frame",
            "data-floor-scope": focused.scope,
            className: "office-game__campusFloorFrame",
            sandbox: "allow-scripts allow-same-origin allow-forms "
              + "allow-downloads allow-popups allow-modals",
            allow: "clipboard-read; clipboard-write",
            loading: "eager",
            src: officeAppSrc({
              base: props.base, theme: props.theme, floor: focused.scope,
            }),
            onLoad: (event: { currentTarget: HTMLIFrameElement }) => {
              const win = event.currentTarget.contentWindow as Window | null;
              if (win) installFloorFrame(win);
            },
          }),
          plaque,
          R.createElement("div", {
            key: "rooms",
            className: "office-game__campusRoomRail",
            "data-testid": "campus-world-rooms",
            role: "group",
            "aria-label": `Rooms on floor ${pad3(focused.number)}`,
          }, rooms.length
            ? rooms.map(roomChip)
            : R.createElement("span", {
                key: "none", className: "office-game__campusRoomMeta",
              }, "No rooms are declared on this floor.")),
        ])
      : null;

    const people = focused?.characters ?? [];
    const peopleBar = focused
      ? R.createElement("div", {
          key: "people", className: "office-game__campusPeople",
          "data-testid": "campus-world-people",
          role: "group",
          "aria-label": `People on floor ${pad3(focused.number)}`,
        }, people.length
          ? [
              R.createElement("span", {
                key: "label", className: "office-game__campusPeopleLabel",
              }, focused.live
                ? "Real agents on this floor"
                : "Unstaffed template"),
              ...people.map(characterChip),
            ]
          : [
              R.createElement("span", {
                key: "label", className: "office-game__campusPeopleLabel",
              }, focused.live ? "Real agents on this floor" : "Unstaffed template"),
              R.createElement("span", {
                key: "none", className: "office-game__campusPeopleEmpty",
                "data-testid": "campus-world-unstaffed",
              }, "No agent is placed on this floor."),
            ])
      : null;

    const announcement = [
      focused ? `Floor ${pad3(focused.number)}, ${focused.name}. `
        + campusFloorStatus(focused) + "." : "",
      focusedRoom ? ` Room ${focusedRoom.name} focused.` : "",
      unplaced ? ` ${unplaced} is on this floor but not in a declared room.` : "",
    ].join("").trim();

    return R.createElement("div", {
      className: "office-game__campusWorld",
      "data-testid": "office-campus-world",
      "data-floor-count": String(floors.length),
      "data-live-floors": String(liveCount),
      "data-rendered-frames": focused ? "1" : "0",
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
          }, `${floors.length} floors · ${liveCount} live · one Habblaud `
            + "floor view at room scale"),
        ]),
        controlButton("prev", "Floor above", "campus-world-prev",
          () => stepFloor(-1), !focused
            || floors.indexOf(focused) <= 0),
        controlButton("next", "Floor below", "campus-world-next",
          () => stepFloor(1), !focused
            || floors.indexOf(focused) >= floors.length - 1),
        controlButton("open", "Open in office", "campus-world-open",
          () => { if (focused) props.onOpenFloor?.(focused); },
          !focused || !props.onOpenFloor),
      ]),
      worldState(),
      R.createElement("div", {
        key: "body", className: "office-game__campusWorldBody",
      }, [
        R.createElement("aside", {
          key: "elevator", className: "office-game__campusElevator",
          "aria-label": "Elevator and floors",
        }, [
          R.createElement("div", {
            key: "head", className: "office-game__campusElevatorHead",
          }, [
            R.createElement("span", { key: "label" }, "Floors"),
            R.createElement("span", {
              key: "count", className: "office-game__campusElevatorCount",
            }, String(floors.length)),
          ]),
          R.createElement("div", {
            key: "list", ref: elevatorRef,
            className: "office-game__campusElevatorList",
            "data-testid": "campus-world-elevator",
            role: "navigation",
            "aria-label": "Switch floor",
          }, floors.map(floorOption)),
        ]),
        R.createElement("div", {
          key: "stage", className: "office-game__campusStageWrap",
        }, [
          R.createElement("div", {
            key: "viewport",
            className: "office-game__campusViewport",
            "data-testid": "campus-world-viewport",
            role: "region",
            "aria-label": focused
              ? `Floor ${pad3(focused.number)} ${focused.name} room view`
              : "Campus floor view",
            tabIndex: 0,
            onKeyDown,
          }, floorScene),
          unplaced
            ? R.createElement("p", {
                key: "unplaced", className: "office-game__campusWorldState",
                role: "status", "data-testid": "campus-world-unplaced",
              }, `${unplaced} is on this floor, but the tower does not place `
                + "them in a declared room. They are not moved to one.")
            : null,
          roomPanel,
          peopleBar,
        ]),
      ]),
      R.createElement("p", {
        key: "announcer", className: "office-game__sr-only",
        "aria-live": "polite", "data-testid": "campus-world-announcer",
      }, announcement),
    ]);
  };
}
