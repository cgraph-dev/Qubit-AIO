/**
 * Qubit Office — Stage 5 room-art schema.
 *
 * This file is a renderer-independent description of the eight rooms visible
 * in the supplied Qubit Office mockup. It does not create a second room
 * renderer, DOM tree, or UI shell. Fittings use room-local tile coordinates;
 * the existing room/camera system supplies the room's world origin.
 *
 * Text remains data for DOM labels. Nothing in this file is canvas text or
 * raster artwork. Runtime occupancy and coordinates from the office payload
 * take precedence over these presentation defaults.
 *
 * ESTIMATED VALUES: room width/height, local fitting coordinates, fitting
 * footprints/elevations, label anchor points, and default seat slots are
 * estimated from the 1679x959 mockup and the required 16px isometric tile.
 * The screenshot is not a coordinate source, so these are editable art-layout
 * defaults rather than claims about live backend floorplan coordinates.
 *
 * Repository alignment:
 * - material IDs follow game/app/room-builder.ts FLOOR_SLUGS and WALL_SLUGS;
 * - palette IDs follow game/app/room-builder.ts PALETTES;
 * - token references are the Stage 1 --office-* names. The current
 *   game/styles/tokens.css is the shell palette and does not declare the Stage 1
 *   surface/ink/gold set, so a live host must supply a Stage 1 token sheet;
 * - animation values follow game/types.ts AnimationState.
 *
 * PORT PROVENANCE: ported verbatim from the canonical ChatGPT Stage 5
 * `rooms.ts` in the original local drawing prototype; the Perplexity alternate schema was not
 * merged. This module is deliberately NOT mounted by the live office, which
 * renders the vendored Habblaud client inside an iframe.
 */

export const ROOM_KINDS = [
  "reception",
  "kitchen",
  "boardroom_3",
  "desk_2",
  "restrooms",
  "lounge",
  "desk_3",
  "fetten",
] as const;

export type RoomKind = (typeof ROOM_KINDS)[number];

/** Existing room-builder floor material identifiers. */
export type FloorMaterial =
  | "floor_wood_light"
  | "floor_wood_dark"
  | "floor_carpet_dark"
  | "floor_tile_grey"
  | "floor_concrete_polished"
  | "floor_marble_warm";

/** Existing room-builder wall material identifiers. */
export type WallMaterial =
  | "wall_panel_light"
  | "wall_plaster_cool"
  | "wall_glass_partition"
  | "wall_tech_panel"
  | "wall_brick_painted"
  | "wall_panel_walnut";

/** Existing room-builder furniture palette IDs; colour ramps resolve in Stage 6. */
export type PaletteId =
  | "cool_teal_neutral"
  | "executive_warm"
  | "finance_navy"
  | "creative_coral"
  | "control_room_amber"
  | "open_office_sand";

/** Only token references already present in the current office token sheet. */
export type OfficeTokenRef =
  | "--office-accent"
  | "--office-warn"
  | "--office-ink"
  | "--office-muted"
  | "--office-ground"
  | "--office-wall"
  | "--office-plate"
  | "--office-line-strong";

export type QuarterTurn = 0 | 90 | 180 | 270;

/** Every procedural fitting kind required by the original Stage 6 brief. */
export type FittingType =
  | "longTable"
  | "boardroomTable"
  | "deskCluster"
  | "receptionDesk"
  | "barCounter"
  | "shelf"
  | "sink"
  | "toilet"
  | "stall"
  | "sofa"
  | "rug"
  | "redCarpet"
  | "lantern"
  | "sakuraTree"
  | "staircase"
  | "sign"
  | "plant"
  | "monitor"
  | "chair";

/**
 * A fitting's gx/gy/w/h are expressed in local 16px tile units. `rug` and
 * `redCarpet` fittings are the drawable forms of the rug/carpet region records;
 * the Stage 6 painter should resolve the matching region by id and paint it
 * once in the floor-surface pass, not a second time in the furniture pass.
 * z is elevation in tile-height units; rotation is clockwise degrees.
 * Palette is a semantic room-builder palette, not a literal colour.
 */
export interface RoomFitting {
  readonly id: string;
  readonly type: FittingType;
  readonly gx: number;
  readonly gy: number;
  readonly w: number;
  readonly h: number;
  readonly z: number;
  readonly rotation: QuarterTurn;
  readonly palette: PaletteId;
}

export type SurfaceRegionKind = "carpet" | "rug";

/** An in-room carpet or rug overlay, placed on the same local tile grid. */
export interface SurfaceRegion {
  readonly id: string;
  readonly kind: SurfaceRegionKind;
  readonly material: FloorMaterial;
  readonly gx: number;
  readonly gy: number;
  readonly w: number;
  readonly h: number;
  readonly rotation: QuarterTurn;
  readonly palette: PaletteId;
}

/** Label text is rendered as DOM by the existing shell, never baked into art. */
export interface RoomLabel {
  readonly text: string;
  /** Local tile point; negative gy positions the primary plate just above the room. */
  readonly gx: number;
  readonly gy: number;
  readonly align: "start" | "center" | "end";
}

export interface RoomSubLabel extends RoomLabel {
  readonly kind: "sub-label";
}

export interface RoomRecipe {
  readonly kind: RoomKind;
  readonly themeId: string;
  readonly width: number;
  readonly height: number;
  readonly floorMaterial: FloorMaterial;
  readonly wallMaterial: WallMaterial;
  /** A design token reference; the renderer resolves its value from tokens.css. */
  readonly trim: OfficeTokenRef;
  readonly carpetRegions: readonly SurfaceRegion[];
  readonly rugRegions: readonly SurfaceRegion[];
  readonly label: RoomLabel;
  readonly subLabels: readonly RoomSubLabel[];
  readonly fittings: readonly RoomFitting[];
}

const fitting = (
  id: string,
  type: FittingType,
  gx: number,
  gy: number,
  w: number,
  h: number,
  z: number,
  rotation: QuarterTurn,
  palette: PaletteId,
): RoomFitting => ({ id, type, gx, gy, w, h, z, rotation, palette });

const region = (
  id: string,
  kind: SurfaceRegionKind,
  material: FloorMaterial,
  gx: number,
  gy: number,
  w: number,
  h: number,
  palette: PaletteId,
  rotation: QuarterTurn = 0,
): SurfaceRegion => ({ id, kind, material, gx, gy, w, h, rotation, palette });

const roomLabel = (text: string, gx: number, gy = -1): RoomLabel => ({
  text,
  gx,
  gy,
  align: "center",
});

const subLabel = (text: string, gx: number, gy: number): RoomSubLabel => ({
  text,
  gx,
  gy,
  align: "center",
  kind: "sub-label",
});

/**
 * Canonical recipes for the eight visible mockup rooms.
 *
 * All local fitting coordinates are deliberately within each room's declared
 * width/height. Any labels or sub-labels outside the floor rectangle are DOM
 * overlays and do not enlarge the walkable room.
 */
export const RECIPES: Record<RoomKind, RoomRecipe> = {
  reception: {
    kind: "reception",
    themeId: "moonlit_reception",
    width: 14,
    height: 8,
    floorMaterial: "floor_wood_dark",
    wallMaterial: "wall_panel_walnut",
    trim: "--office-warn",
    carpetRegions: [
      region("reception-entry-red-carpet", "carpet", "floor_carpet_dark", 0, 6, 14, 2, "executive_warm"),
    ],
    rugRegions: [
      region("reception-desk-rug", "rug", "floor_marble_warm", 1, 1, 6, 4, "executive_warm"),
    ],
    label: roomLabel("Reception", 7),
    subLabels: [],
    fittings: [
      fitting("reception-entry-red-carpet", "redCarpet", 0, 6, 14, 2, 0, 0, "executive_warm"),
      fitting("reception-counter", "receptionDesk", 2, 2, 4, 1, 0, 0, "executive_warm"),
      fitting("reception-monitor", "monitor", 4, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("reception-chair", "chair", 3, 4, 1, 1, 0, 180, "executive_warm"),
      fitting("reception-shelf", "shelf", 9, 1, 3, 1, 0, 0, "executive_warm"),
      fitting("reception-lantern-west", "lantern", 1, 1, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("reception-lantern-east", "lantern", 12, 5, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("reception-sakura", "sakuraTree", 10, 5, 3, 3, 0, 0, "creative_coral"),
      fitting("reception-plant", "plant", 7, 5, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("reception-sign", "sign", 6, 0, 2, 1, 1.2, 0, "executive_warm"),
    ],
  },

  kitchen: {
    kind: "kitchen",
    themeId: "sakura_kitchen",
    width: 14,
    height: 8,
    floorMaterial: "floor_tile_grey",
    wallMaterial: "wall_plaster_cool",
    trim: "--office-accent",
    carpetRegions: [],
    rugRegions: [],
    label: roomLabel("Kitchen", 7),
    subLabels: [],
    fittings: [
      fitting("kitchen-bar", "barCounter", 1, 1, 5, 1, 0, 0, "executive_warm"),
      fitting("kitchen-bar-monitor", "monitor", 4, 1, 1, 1, 0.55, 0, "control_room_amber"),
      fitting("kitchen-long-table", "longTable", 2, 4, 6, 1, 0, 0, "open_office_sand"),
      fitting("kitchen-chair-west", "chair", 1, 4, 1, 1, 0, 90, "open_office_sand"),
      fitting("kitchen-chair-east", "chair", 8, 4, 1, 1, 0, 270, "open_office_sand"),
      fitting("kitchen-shelf", "shelf", 9, 1, 3, 1, 0, 0, "executive_warm"),
      fitting("kitchen-plant", "plant", 11, 5, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("kitchen-lantern", "lantern", 12, 5, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("kitchen-sign", "sign", 6, 0, 2, 1, 1.2, 0, "creative_coral"),
    ],
  },

  boardroom_3: {
    kind: "boardroom_3",
    themeId: "aubergine_boardroom",
    width: 14,
    height: 8,
    floorMaterial: "floor_marble_warm",
    wallMaterial: "wall_panel_walnut",
    trim: "--office-warn",
    carpetRegions: [
      region("boardroom-red-runner", "carpet", "floor_carpet_dark", 2, 3, 10, 2, "finance_navy"),
    ],
    rugRegions: [],
    label: roomLabel("boardroom 3", 7),
    subLabels: [
      subLabel("seo content", 9, 1),
      subLabel("desk", 7, 6),
    ],
    fittings: [
      fitting("boardroom-red-runner", "redCarpet", 2, 3, 10, 2, 0, 0, "finance_navy"),
      fitting("boardroom-table", "boardroomTable", 3, 3, 7, 2, 0, 0, "executive_warm"),
      fitting("boardroom-monitor", "monitor", 6, 2, 1, 1, 0.7, 0, "control_room_amber"),
      fitting("boardroom-chair-nw", "chair", 3, 2, 1, 1, 0, 0, "executive_warm"),
      fitting("boardroom-chair-ne", "chair", 9, 2, 1, 1, 0, 0, "executive_warm"),
      fitting("boardroom-chair-sw", "chair", 3, 6, 1, 1, 0, 180, "executive_warm"),
      fitting("boardroom-chair-se", "chair", 9, 6, 1, 1, 0, 180, "executive_warm"),
      fitting("boardroom-chair-west", "chair", 2, 4, 1, 1, 0, 90, "executive_warm"),
      fitting("boardroom-chair-east", "chair", 10, 4, 1, 1, 0, 270, "executive_warm"),
      fitting("boardroom-shelf", "shelf", 11, 1, 2, 1, 0, 0, "finance_navy"),
      fitting("boardroom-lantern", "lantern", 12, 6, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("boardroom-sign", "sign", 6, 0, 2, 1, 1.2, 0, "executive_warm"),
    ],
  },

  desk_2: {
    kind: "desk_2",
    themeId: "rosewood_open_office",
    width: 14,
    height: 8,
    floorMaterial: "floor_tile_grey",
    wallMaterial: "wall_glass_partition",
    trim: "--office-accent",
    carpetRegions: [],
    rugRegions: [],
    label: roomLabel("desk 2", 7),
    subLabels: [
      subLabel("desk", 4, 1),
      subLabel("aio_silo", 10, 5),
    ],
    fittings: [
      fitting("desk2-cluster-west", "deskCluster", 1, 2, 4, 3, 0, 0, "creative_coral"),
      fitting("desk2-cluster-east", "deskCluster", 8, 2, 4, 3, 0, 0, "open_office_sand"),
      fitting("desk2-monitor-1", "monitor", 2, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk2-monitor-2", "monitor", 4, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk2-monitor-3", "monitor", 9, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk2-monitor-4", "monitor", 11, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk2-chair-1", "chair", 2, 5, 1, 1, 0, 180, "creative_coral"),
      fitting("desk2-chair-2", "chair", 4, 5, 1, 1, 0, 180, "creative_coral"),
      fitting("desk2-chair-3", "chair", 9, 5, 1, 1, 0, 180, "open_office_sand"),
      fitting("desk2-chair-4", "chair", 11, 5, 1, 1, 0, 180, "open_office_sand"),
      fitting("desk2-shelf", "shelf", 5, 0, 3, 1, 0, 0, "finance_navy"),
      fitting("desk2-plant", "plant", 12, 6, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("desk2-lantern", "lantern", 1, 6, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("desk2-sign", "sign", 6, 0, 2, 1, 1.2, 0, "creative_coral"),
    ],
  },

  restrooms: {
    kind: "restrooms",
    themeId: "moonstone_restrooms",
    width: 12,
    height: 8,
    floorMaterial: "floor_tile_grey",
    wallMaterial: "wall_plaster_cool",
    trim: "--office-line-strong",
    carpetRegions: [],
    rugRegions: [],
    label: roomLabel("Restrooms", 6),
    subLabels: [],
    fittings: [
      fitting("restroom-stall-west", "stall", 1, 1, 2, 3, 0, 0, "cool_teal_neutral"),
      fitting("restroom-stall-east", "stall", 4, 1, 2, 3, 0, 0, "cool_teal_neutral"),
      fitting("restroom-toilet-west", "toilet", 1, 2, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("restroom-toilet-east", "toilet", 4, 2, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("restroom-sink-west", "sink", 8, 1, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("restroom-sink-east", "sink", 10, 1, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("restroom-shelf", "shelf", 7, 5, 3, 1, 0, 0, "finance_navy"),
      fitting("restroom-lantern", "lantern", 10, 6, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("restroom-sign", "sign", 5, 0, 2, 1, 1.2, 0, "cool_teal_neutral"),
    ],
  },

  lounge: {
    kind: "lounge",
    themeId: "sakura_lounge",
    width: 14,
    height: 8,
    floorMaterial: "floor_wood_dark",
    wallMaterial: "wall_brick_painted",
    trim: "--office-accent",
    carpetRegions: [],
    rugRegions: [
      region("lounge-main-rug-fitting", "rug", "floor_carpet_dark", 2, 2, 9, 4, "creative_coral"),
    ],
    label: roomLabel("Lounge", 7),
    subLabels: [],
    fittings: [
      fitting("lounge-main-rug-fitting-fitting", "rug", 2, 2, 9, 4, 0, 0, "creative_coral"),
      fitting("lounge-sofa-west", "sofa", 1, 2, 3, 1, 0, 0, "creative_coral"),
      fitting("lounge-sofa-east", "sofa", 9, 2, 3, 1, 0, 180, "creative_coral"),
      fitting("lounge-long-table", "longTable", 4, 4, 4, 1, 0, 0, "executive_warm"),
      fitting("lounge-shelf", "shelf", 1, 6, 3, 1, 0, 0, "finance_navy"),
      fitting("lounge-staircase", "staircase", 11, 4, 2, 3, 0, 0, "executive_warm"),
      fitting("lounge-sakura", "sakuraTree", 1, 5, 3, 3, 0, 0, "creative_coral"),
      fitting("lounge-plant", "plant", 12, 1, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("lounge-lantern-west", "lantern", 2, 1, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("lounge-lantern-east", "lantern", 11, 6, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("lounge-sign", "sign", 6, 0, 2, 1, 1.2, 0, "creative_coral"),
    ],
  },

  desk_3: {
    kind: "desk_3",
    themeId: "golden_desk_floor",
    width: 14,
    height: 8,
    floorMaterial: "floor_marble_warm",
    wallMaterial: "wall_glass_partition",
    trim: "--office-warn",
    carpetRegions: [],
    rugRegions: [],
    label: roomLabel("desk 3", 7),
    subLabels: [],
    fittings: [
      fitting("desk3-cluster-west", "deskCluster", 1, 2, 4, 3, 0, 0, "finance_navy"),
      fitting("desk3-cluster-east", "deskCluster", 8, 2, 4, 3, 0, 0, "executive_warm"),
      fitting("desk3-monitor-1", "monitor", 2, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk3-monitor-2", "monitor", 4, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk3-monitor-3", "monitor", 9, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk3-monitor-4", "monitor", 11, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("desk3-chair-1", "chair", 2, 5, 1, 1, 0, 180, "finance_navy"),
      fitting("desk3-chair-2", "chair", 4, 5, 1, 1, 0, 180, "finance_navy"),
      fitting("desk3-chair-3", "chair", 9, 5, 1, 1, 0, 180, "executive_warm"),
      fitting("desk3-chair-4", "chair", 11, 5, 1, 1, 0, 180, "executive_warm"),
      fitting("desk3-shelf", "shelf", 5, 0, 3, 1, 0, 0, "finance_navy"),
      fitting("desk3-plant", "plant", 12, 6, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("desk3-lantern", "lantern", 1, 6, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("desk3-sign", "sign", 6, 0, 2, 1, 1.2, 0, "executive_warm"),
    ],
  },

  fetten: {
    kind: "fetten",
    themeId: "fetten_amber_suite",
    width: 14,
    height: 8,
    floorMaterial: "floor_wood_light",
    wallMaterial: "wall_panel_light",
    trim: "--office-warn",
    carpetRegions: [
      region("fetten-entry-red-carpet", "carpet", "floor_carpet_dark", 0, 6, 14, 1, "executive_warm"),
    ],
    rugRegions: [],
    label: roomLabel("fetten", 7),
    subLabels: [],
    fittings: [
      fitting("fetten-entry-red-carpet", "redCarpet", 0, 6, 14, 1, 0, 0, "executive_warm"),
      fitting("fetten-desk-cluster-west", "deskCluster", 1, 2, 4, 3, 0, 0, "open_office_sand"),
      fitting("fetten-desk-cluster-east", "deskCluster", 8, 2, 4, 3, 0, 0, "executive_warm"),
      fitting("fetten-monitor-1", "monitor", 2, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("fetten-monitor-2", "monitor", 4, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("fetten-monitor-3", "monitor", 9, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("fetten-monitor-4", "monitor", 11, 2, 1, 1, 0.65, 0, "control_room_amber"),
      fitting("fetten-chair-1", "chair", 2, 5, 1, 1, 0, 180, "open_office_sand"),
      fitting("fetten-chair-2", "chair", 4, 5, 1, 1, 0, 180, "open_office_sand"),
      fitting("fetten-chair-3", "chair", 9, 5, 1, 1, 0, 180, "executive_warm"),
      fitting("fetten-chair-4", "chair", 11, 5, 1, 1, 0, 180, "executive_warm"),
      fitting("fetten-shelf", "shelf", 5, 0, 3, 1, 0, 0, "finance_navy"),
      fitting("fetten-plant", "plant", 12, 6, 1, 1, 0, 0, "cool_teal_neutral"),
      fitting("fetten-lantern", "lantern", 1, 6, 1, 1, 0.1, 0, "control_room_amber"),
      fitting("fetten-sign", "sign", 6, 0, 2, 1, 1.2, 0, "executive_warm"),
    ],
  },
};

/**
 * The source mockup's roster, grouped by its visible room labels. These records
 * are presentation fixtures only: a live OfficeTowerPayload.characters array
 * remains authoritative for real agent IDs, positions, and animation state.
 */
export type AgentId =
  | "zen"
  | "jie"
  | "lead-quality"
  | "sable"
  | "mira"
  | "kerr"
  | "wren"
  | "alex"
  | "juno"
  | "tobi";

export type AgentAnimation =
  | "idle"
  | "working"
  | "walking"
  | "meeting"
  | "blocked"
  | "carrying"
  | "glow"
  | "settling"
  | "walking_to_manager"
  | "seated";

export interface OfficeAgentArtData {
  readonly id: AgentId;
  readonly displayName: string;
  readonly room: RoomKind;
  readonly slot: number;
  readonly animation: AgentAnimation;
  /** Human-readable state copied from the mockup's style of status line. */
  readonly statusText: string;
  /** Present only where the mockup shows an instruction-waiting detail. */
  readonly detailText: "Waiting for instructions" | null;
  readonly palette: PaletteId;
}

export const AGENTS: Record<AgentId, OfficeAgentArtData> = {
  zen: {
    id: "zen",
    displayName: "Zen",
    room: "boardroom_3",
    slot: 0,
    animation: "idle",
    statusText: "idle 31d 8h",
    detailText: null,
    palette: "executive_warm",
  },
  jie: {
    id: "jie",
    displayName: "Jie",
    room: "boardroom_3",
    slot: 1,
    animation: "idle",
    statusText: "idle 31d 8h",
    detailText: null,
    palette: "creative_coral",
  },
  "lead-quality": {
    id: "lead-quality",
    displayName: "lead-quality",
    room: "boardroom_3",
    slot: 2,
    animation: "idle",
    statusText: "idle 31d 8h",
    detailText: "Waiting for instructions",
    palette: "finance_navy",
  },
  sable: {
    id: "sable",
    displayName: "sable",
    room: "desk_2",
    slot: 0,
    animation: "idle",
    statusText: "idle 31d 8h",
    detailText: "Waiting for instructions",
    palette: "executive_warm",
  },
  mira: {
    id: "mira",
    displayName: "mira",
    room: "desk_2",
    slot: 1,
    animation: "idle",
    statusText: "idle 31d 0h",
    detailText: "Waiting for instructions",
    palette: "cool_teal_neutral",
  },
  kerr: {
    id: "kerr",
    displayName: "kerr",
    room: "desk_2",
    slot: 2,
    animation: "idle",
    statusText: "idle 31d 0h",
    detailText: "Waiting for instructions",
    palette: "finance_navy",
  },
  wren: {
    id: "wren",
    displayName: "wren",
    room: "desk_2",
    slot: 3,
    animation: "idle",
    statusText: "Waiting for instructions",
    detailText: null,
    palette: "creative_coral",
  },
  alex: {
    id: "alex",
    displayName: "alex",
    room: "desk_3",
    slot: 0,
    animation: "idle",
    statusText: "idle 31d 1h",
    detailText: "Waiting for instructions",
    palette: "finance_navy",
  },
  juno: {
    id: "juno",
    displayName: "juno",
    room: "desk_3",
    slot: 1,
    animation: "idle",
    statusText: "idle 31d 1h",
    detailText: "Waiting for instructions",
    palette: "creative_coral",
  },
  tobi: {
    id: "tobi",
    displayName: "tobi",
    room: "desk_3",
    slot: 2,
    animation: "idle",
    statusText: "idle 31d 1h",
    detailText: "Waiting for instructions",
    palette: "executive_warm",
  },
};

/** Explicit room roster order, matching the left-panel grouping in the image. */
export const AGENTS_BY_ROOM: Record<RoomKind, readonly AgentId[]> = {
  reception: [],
  kitchen: [],
  boardroom_3: ["zen", "jie", "lead-quality"],
  desk_2: ["sable", "mira", "kerr", "wren"],
  restrooms: [],
  lounge: [],
  desk_3: ["alex", "juno", "tobi"],
  fetten: [],
};

/** Room label strings rendered by the existing DOM label surface. */
export const ROOM_LABELS: Record<RoomKind, string> = {
  reception: "Reception",
  kitchen: "Kitchen",
  boardroom_3: "boardroom 3",
  desk_2: "desk 2",
  restrooms: "Restrooms",
  lounge: "Lounge",
  desk_3: "desk 3",
  fetten: "fetten",
};

/** Only sub-label strings visible in the supplied mockup. */
export const ROOM_SUB_LABELS: Partial<Record<RoomKind, readonly string[]>> = {
  boardroom_3: ["seo content", "desk"],
  desk_2: ["desk", "aio_silo"],
};

/**
 * Compile-time-safe lookups used by the later draw stages.
 * Throws are avoided here; callers can handle missing runtime room kinds.
 */
export function recipeFor(kind: RoomKind): RoomRecipe {
  return RECIPES[kind];
}

export function agentsForRoom(kind: RoomKind): readonly OfficeAgentArtData[] {
  return AGENTS_BY_ROOM[kind].map((id) => AGENTS[id]);
}
