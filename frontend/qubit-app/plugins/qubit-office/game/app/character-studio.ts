/* Character Studio model — ONE canonical profile, projections are disposable.
 *
 * The studio edits `CharacterProfile` and nothing else. There is no second
 * editor, no localStorage draft and no raw paid art here: parts are ids/labels,
 * the preview is a palette swatch, and the only write is one save call to the
 * Business OS. A missing part falls back deterministically and the substitution
 * is reported to the user instead of silently changing the character.
 */
import { BusinessApiError, businessFetch } from "./business-api";

export const PART_CATEGORIES = [
  "body", "skin", "eyes", "hair", "outfit",
] as const;
export type PartCategory = (typeof PART_CATEGORIES)[number] | "accessory";

export const PART_COUNTS: Record<string, number> = {
  body: 9,
  skin: 9,
  eyes: 7,
  hair: 29,
  outfit: 33,
  accessory: 6,
};

export const ANIMATIONS = [
  "idle", "walk", "type", "talk", "think", "break", "celebrate",
] as const;
export type AnimationName = (typeof ANIMATIONS)[number];

export const LAYER_ORDER = [
  "body", "outfit", "skin", "eyes", "hair", "accessory",
] as const;
export type LayerName = (typeof LAYER_ORDER)[number];

export const SHADOW_POLICIES = ["none", "sprite", "room"] as const;
export type ShadowPolicy = (typeof SHADOW_POLICIES)[number];

export const PART_CATALOG: Part[] = buildPartCatalog();

export interface Part {
  part_id: string;
  category: PartCategory;
  index: number;
  label: string;
}

export interface CharacterProfile {
  profile_id: string;
  agent_id: string;
  body_id: string;
  skin_id: string;
  eyes_id: string;
  hair_id: string;
  outfit_id: string;
  accessory_ids: string[];
  palette: string[];
  layer_order: LayerName[];
  shadow_policy: ShadowPolicy;
  portrait_cell: string;
  portrait_size: 32 | 48;
  animation_set: AnimationName[];
  style_version: number;
  seed: number;
  revision: number;
}

export interface Fallback {
  category: string;
  from: string;
  to: string;
  reason: string;
}

export interface StudioState {
  draft: CharacterProfile;
  history: CharacterProfile[];
  future: CharacterProfile[];
  locks: PartCategory[];
  selectedCategory: PartCategory;
  search: string;
  previewAnimation: AnimationName;
  portraitSize: 32 | 48;
  fallbacks: Fallback[];
}

export type SaveResult =
  | { kind: "saved"; profile: CharacterProfile }
  | { kind: "error"; code: string; detail: string };

const PART_INDEX = new Map(PART_CATALOG.map((part) => [part.part_id, part]));
const DEFAULT_PALETTE = ["#1f2933", "#3e4c59", "#f0b429", "#7fb3d5"];

export function buildPartCatalog(): Part[] {
  const parts: Part[] = [];
  for (const category of [...PART_CATEGORIES, "accessory"] as PartCategory[]) {
    const label = category.charAt(0).toUpperCase()
      + category.slice(1);
    for (let index = 0; index < PART_COUNTS[category]; index += 1) {
      parts.push({
        part_id: `${category}-${String(index).padStart(2, "0")}`,
        category,
        index,
        label: `${label} ${String(index + 1).padStart(2, "0")}`,
      });
    }
  }
  return parts;
}

export function partCounts(catalog: Part[] = PART_CATALOG): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const part of catalog) {
    counts[part.category] = (counts[part.category] ?? 0) + 1;
  }
  return counts;
}

export function partsFor(
  category: PartCategory,
  catalog: Part[] = PART_CATALOG,
): Part[] {
  return catalog.filter((part) => part.category === category);
}

export function searchParts(
  category: PartCategory,
  query: string,
  catalog: Part[] = PART_CATALOG,
): Part[] {
  const needle = query.trim().toLowerCase();
  const parts = partsFor(category, catalog);
  if (!needle) return parts;
  return parts.filter((part) =>
    part.part_id.toLowerCase().includes(needle)
    || part.label.toLowerCase().includes(needle));
}

function isLayerOrder(value: unknown): value is LayerName[] {
  if (!Array.isArray(value) || value.length !== LAYER_ORDER.length) {
    return false;
  }
  const set = new Set(value);
  return set.size === LAYER_ORDER.length
    && LAYER_ORDER.every((layer) => set.has(layer));
}

function coerceProfile(input: Partial<CharacterProfile> | null | undefined):
CharacterProfile {
  const base = input ?? {};
  return {
    profile_id: String(base.profile_id ?? "profile-draft"),
    agent_id: String(base.agent_id ?? "agent-draft"),
    body_id: String(base.body_id ?? "body-00"),
    skin_id: String(base.skin_id ?? "skin-00"),
    eyes_id: String(base.eyes_id ?? "eyes-00"),
    hair_id: String(base.hair_id ?? "hair-00"),
    outfit_id: String(base.outfit_id ?? "outfit-00"),
    accessory_ids: Array.isArray(base.accessory_ids)
      ? base.accessory_ids.map(String) : [],
    palette: Array.isArray(base.palette) && base.palette.length
      ? base.palette.map(String) : [...DEFAULT_PALETTE],
    layer_order: isLayerOrder(base.layer_order)
      ? base.layer_order.slice() : [...LAYER_ORDER],
    shadow_policy: SHADOW_POLICIES.includes(base.shadow_policy as ShadowPolicy)
      ? base.shadow_policy as ShadowPolicy : "sprite",
    portrait_cell: String(base.portrait_cell ?? "cell-0"),
    portrait_size: base.portrait_size === 48 ? 48 : 32,
    animation_set: Array.isArray(base.animation_set)
      && base.animation_set.length ? base.animation_set.slice() : [...ANIMATIONS],
    style_version: Number.isFinite(base.style_version)
      ? Number(base.style_version) : 1,
    seed: Number.isFinite(base.seed) ? Number(base.seed) : 0,
    revision: Number.isFinite(base.revision) ? Number(base.revision) : 0,
  };
}

/** Fill defaults and report every missing/renamed part substitution. */
export function resolveProfile(
  input: Partial<CharacterProfile> | null | undefined,
  catalog: Part[] = PART_CATALOG,
): { profile: CharacterProfile; fallbacks: Fallback[] } {
  const index = new Map(catalog.map((part) => [part.part_id, part]));
  const profile = coerceProfile(input);
  const fallbacks: Fallback[] = [];

  for (const category of PART_CATEGORIES) {
    const key = `${category}_id` as keyof CharacterProfile;
    const current = String(profile[key]);
    const part = index.get(current);
    if (!part || part.category !== category) {
      const fallback = catalog.find((row) => row.category === category);
      if (fallback) {
        (profile as unknown as Record<string, unknown>)[key] = fallback.part_id;
        fallbacks.push({
          category, from: current, to: fallback.part_id,
          reason: "missing_part",
        });
      }
    }
  }

  profile.accessory_ids = profile.accessory_ids.filter((id) => {
    const part = index.get(id);
    if (part && part.category === "accessory") return true;
    fallbacks.push({
      category: "accessory", from: id, to: "", reason: "missing_part",
    });
    return false;
  });

  const animations = profile.animation_set.filter(
    (animation) => (ANIMATIONS as readonly string[]).includes(animation));
  if (!animations.length || !animations.includes("idle")) {
    fallbacks.push({
      category: "animation_set",
      from: animations.join(","),
      to: ANIMATIONS.join(","),
      reason: "invalid_animation_set",
    });
    profile.animation_set = [...ANIMATIONS];
  } else {
    profile.animation_set = animations;
  }

  const declaredLayers = profile.layer_order as unknown as string[];
  if (!isLayerOrder(profile.layer_order)) {
    fallbacks.push({
      category: "layer_order",
      from: declaredLayers.join(","),
      to: LAYER_ORDER.join(","),
      reason: "invalid_layer_order",
    });
    profile.layer_order = [...LAYER_ORDER];
  }

  if (profile.portrait_size !== 32 && profile.portrait_size !== 48) {
    fallbacks.push({
      category: "portrait_size",
      from: String(profile.portrait_size),
      to: "32",
      reason: "invalid_portrait_size",
    });
    profile.portrait_size = 32;
  }

  return { profile, fallbacks };
}

export function createStudioState(
  input?: Partial<CharacterProfile> | null,
  catalog: Part[] = PART_CATALOG,
): StudioState {
  const { profile, fallbacks } = resolveProfile(input, catalog);
  return {
    draft: profile,
    history: [],
    future: [],
    locks: [],
    selectedCategory: "body",
    search: "",
    previewAnimation: "idle",
    portraitSize: profile.portrait_size,
    fallbacks,
  };
}

function revised(state: StudioState, draft: CharacterProfile): StudioState {
  return {
    ...state,
    draft,
    history: [...state.history, state.draft].slice(-50),
    future: [],
  };
}

export function selectCategory(state: StudioState,
                               category: PartCategory): StudioState {
  return { ...state, selectedCategory: category, search: "" };
}

export function setSearch(state: StudioState, search: string): StudioState {
  return { ...state, search };
}

export function pickPart(state: StudioState, category: PartCategory,
                         partId: string): StudioState {
  const part = PART_INDEX.get(partId);
  if (!part || part.category !== category) return state;
  if (category === "accessory") {
    const next = state.draft.accessory_ids.includes(partId)
      ? state.draft.accessory_ids.filter((id) => id !== partId)
      : [...state.draft.accessory_ids, partId];
    return revised(state, { ...state.draft, accessory_ids: next });
  }
  const key = `${category}_id` as keyof CharacterProfile;
  if (state.draft[key] === partId) return state;
  return revised(state, { ...state.draft, [key]: partId });
}

export function toggleLock(state: StudioState,
                           category: PartCategory): StudioState {
  const locks = state.locks.includes(category)
    ? state.locks.filter((row) => row !== category)
    : [...state.locks, category];
  return { ...state, locks };
}

function seededRandom(seed: number): () => number {
  let value = seed >>> 0;
  return () => {
    value = (Math.imul(1664525, value) + 1013904223) >>> 0;
    return value / 0x100000000;
  };
}

export function randomize(state: StudioState, seed?: number,
                          catalog: Part[] = PART_CATALOG): StudioState {
  const random = seed === undefined ? Math.random : seededRandom(seed);
  const draft: CharacterProfile = {
    ...state.draft,
    accessory_ids: state.draft.accessory_ids.slice(),
  };
  for (const category of PART_CATEGORIES) {
    if (state.locks.includes(category)) continue;
    const parts = partsFor(category, catalog);
    if (!parts.length) continue;
    const part = parts[Math.floor(random() * parts.length)];
    (draft as unknown as Record<string, unknown>)[`${category}_id`]
      = part.part_id;
  }
  if (!state.locks.includes("accessory")) {
    const accessories = partsFor("accessory", catalog);
    const keep = accessories.filter(() => random() < 0.2);
    draft.accessory_ids = keep.map((part) => part.part_id);
  }
  draft.seed = seed === undefined ? Math.floor(random() * 1_000_000) : seed;
  return revised(state, draft);
}

export function undo(state: StudioState): StudioState {
  if (!state.history.length) return state;
  const previous = state.history[state.history.length - 1];
  return {
    ...state,
    draft: previous,
    history: state.history.slice(0, -1),
    future: [state.draft, ...state.future],
  };
}

export function redo(state: StudioState): StudioState {
  if (!state.future.length) return state;
  const [next, ...rest] = state.future;
  return {
    ...state,
    draft: next,
    history: [...state.history, state.draft],
    future: rest,
  };
}

export interface Preset {
  preset_id: string;
  label: string;
  picks: Partial<Record<`${PartCategory}_id`, string>>;
  accessory_ids?: string[];
}

export const PRESETS: Preset[] = [
  {
    preset_id: "analyst", label: "Analyst",
    picks: { body_id: "body-01", hair_id: "hair-03",
      outfit_id: "outfit-04", eyes_id: "eyes-01" },
  },
  {
    preset_id: "engineer", label: "Engineer",
    picks: { body_id: "body-04", hair_id: "hair-11",
      outfit_id: "outfit-12", skin_id: "skin-02" },
  },
  {
    preset_id: "studio", label: "Studio",
    picks: { body_id: "body-07", hair_id: "hair-20",
      outfit_id: "outfit-25", eyes_id: "eyes-05" },
    accessory_ids: ["accessory-02"],
  },
];

export function applyPreset(state: StudioState, presetId: string,
                            catalog: Part[] = PART_CATALOG): StudioState {
  const preset = PRESETS.find((row) => row.preset_id === presetId);
  if (!preset) return state;
  const index = new Map(catalog.map((part) => [part.part_id, part]));
  const draft: CharacterProfile = { ...state.draft };
  for (const [key, value] of Object.entries(preset.picks)) {
    const part = value ? index.get(value) : undefined;
    if (part) {
      (draft as unknown as Record<string, unknown>)[key] = part.part_id;
    }
  }
  if (preset.accessory_ids) {
    draft.accessory_ids = preset.accessory_ids.filter((id) =>
      index.get(id)?.category === "accessory");
  }
  return revised(state, draft);
}

export function setPaletteColor(state: StudioState, index: number,
                                colour: string): StudioState {
  if (!/^#[0-9a-fA-F]{6}$/.test(colour)) return state;
  if (index < 0 || index >= state.draft.palette.length) return state;
  const palette = state.draft.palette.slice();
  palette[index] = colour.toLowerCase();
  return revised(state, { ...state.draft, palette });
}

export function setShadowPolicy(state: StudioState,
                                policy: ShadowPolicy): StudioState {
  if (!SHADOW_POLICIES.includes(policy)) return state;
  return revised(state, { ...state.draft, shadow_policy: policy });
}

export function moveLayer(state: StudioState, layer: LayerName,
                          direction: "up" | "down"): StudioState {
  const order = state.draft.layer_order.slice();
  const index = order.indexOf(layer);
  if (index < 0) return state;
  const target = direction === "up" ? index - 1 : index + 1;
  if (target < 0 || target >= order.length) return state;
  [order[index], order[target]] = [order[target], order[index]];
  return revised(state, { ...state.draft, layer_order: order });
}

export function setPortraitSize(state: StudioState,
                                size: 32 | 48): StudioState {
  return { ...state, portraitSize: size,
    draft: { ...state.draft, portrait_size: size } };
}

export function setPreviewAnimation(state: StudioState,
                                    animation: AnimationName): StudioState {
  if (!(ANIMATIONS as readonly string[]).includes(animation)) return state;
  return { ...state, previewAnimation: animation };
}

export function profileEquals(a: CharacterProfile,
                              b: CharacterProfile): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function selectedPartId(state: StudioState,
                               category: PartCategory = state.selectedCategory):
string {
  if (category === "accessory") return "";
  return String(state.draft[`${category}_id` as keyof CharacterProfile]);
}

export interface SaveOptions {
  base?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
}

/**
 * The ONE save call. It posts the canonical profile to the Business OS and
 * never writes a local copy. A typed refusal is returned as an error state.
 */
export async function saveCharacterProfile(
  profile: CharacterProfile,
  opts: SaveOptions = {},
): Promise<SaveResult> {
  try {
    const body = await businessFetch<{
      ok?: boolean;
      profile?: Partial<CharacterProfile>;
    }>("/character-profiles", {
      method: "POST",
      body: { profile },
      base: opts.base,
      fetchImpl: opts.fetchImpl,
      signal: opts.signal,
    });
    const saved = body.profile
      ? resolveProfile({ ...profile, ...body.profile }).profile
      : profile;
    return { kind: "saved", profile: saved };
  } catch (exc) {
    if (exc instanceof BusinessApiError) {
      return {
        kind: "error",
        code: exc.code || `HTTP_${exc.status}`,
        detail: exc.message,
      };
    }
    return {
      kind: "error",
      code: "CHARACTER_SAVE_FAILED",
      detail: exc instanceof Error ? exc.message : String(exc),
    };
  }
}

export function liveText(state: StudioState): string {
  const category = state.selectedCategory;
  const picked = category === "accessory"
    ? (state.draft.accessory_ids.length
      ? `${state.draft.accessory_ids.length} accessories selected`
      : "no accessories selected")
    : `${PART_INDEX.get(selectedPartId(state))?.label ?? "unknown part"}`;
  return `Character ${state.draft.profile_id}; category ${category}; `
    + `${picked}; ${partCounts()[category] ?? 0} options; `
    + `portrait ${state.portraitSize}; animation ${state.previewAnimation}`;
}

