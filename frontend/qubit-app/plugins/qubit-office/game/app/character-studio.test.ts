import { describe, expect, it } from "vitest";
import { recordingFetch, respond } from "./business-test-harness";
import {
  ANIMATIONS,
  LAYER_ORDER,
  PART_CATALOG,
  PART_COUNTS,
  PRESETS,
  applyPreset,
  buildPartCatalog,
  createStudioState,
  liveText,
  moveLayer,
  partCounts,
  pickPart,
  randomize,
  redo,
  resolveProfile,
  saveCharacterProfile,
  searchParts,
  selectCategory,
  setPaletteColor,
  setPortraitSize,
  setPreviewAnimation,
  setSearch,
  setShadowPolicy,
  toggleLock,
  undo,
} from "./character-studio";

describe("character catalog", () => {
  it("matches the measured LimeZu counts and unique ids", () => {
    const counts = partCounts(buildPartCatalog());
    expect(counts.body).toBe(9);
    expect(counts.skin).toBe(9);
    expect(counts.eyes).toBe(7);
    expect(counts.hair).toBe(29);
    expect(counts.outfit).toBe(33);
    expect(counts.accessory).toBe(6);
    const ids = PART_CATALOG.map((part) => part.part_id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(PART_CATALOG.every((part) => !part.label.includes("/"))).toBe(true);
  });

  it("never exposes a local paid path in a part", () => {
    const serialised = JSON.stringify(PART_CATALOG);
    expect(serialised).not.toContain("/home/trick");
    expect(serialised).not.toContain(".png");
  });
});

describe("studio draft", () => {
  it("falls back missing parts and reports every substitution", () => {
    const { profile, fallbacks } = resolveProfile({
      profile_id: "profile-a",
      body_id: "body-999",
      accessory_ids: ["accessory-999"],
    });
    expect(profile.body_id).toBe("body-00");
    expect(profile.accessory_ids).toEqual([]);
    expect(fallbacks.map((row) => row.category)).toContain("body");
    expect(fallbacks.map((row) => row.category)).toContain("accessory");
  });

  it("selects and searches parts within a category", () => {
    let state = createStudioState();
    state = selectCategory(state, "hair");
    expect(state.selectedCategory).toBe("hair");
    state = setSearch(state, "Hair 03");
    const parts = searchParts(state.selectedCategory, state.search);
    expect(parts).toHaveLength(1);
    expect(parts[0].part_id).toBe("hair-02");
    state = pickPart(state, "hair", "hair-02");
    expect(state.draft.hair_id).toBe("hair-02");
  });

  it("toggles accessories instead of replacing them", () => {
    let state = createStudioState();
    state = pickPart(state, "accessory", "accessory-01");
    state = pickPart(state, "accessory", "accessory-02");
    expect(state.draft.accessory_ids).toEqual([
      "accessory-01", "accessory-02"]);
    state = pickPart(state, "accessory", "accessory-01");
    expect(state.draft.accessory_ids).toEqual(["accessory-02"]);
  });

  it("randomizes deterministically and respects locks", () => {
    const first = randomize(createStudioState(), 12345);
    const second = randomize(createStudioState(), 12345);
    expect(first.draft).toEqual(second.draft);
    expect(first.draft.seed).toBe(12345);

    let locked = createStudioState();
    locked = toggleLock(locked, "body");
    const before = locked.draft.body_id;
    const rolled = randomize(locked, 99);
    expect(rolled.draft.body_id).toBe(before);
    expect(rolled.locks).toContain("body");
  });

  it("undoes and redoes a change", () => {
    let state = createStudioState();
    state = pickPart(state, "hair", "hair-01");
    state = undo(state);
    expect(state.draft.hair_id).toBe("hair-00");
    state = redo(state);
    expect(state.draft.hair_id).toBe("hair-01");
    expect(redo(state)).toBe(state);
    expect(undo(createStudioState())).toEqual(createStudioState());
  });

  it("applies presets, palette, shadow, layer and preview changes", () => {
    let state = createStudioState();
    state = applyPreset(state, "analyst");
    expect(state.draft.body_id).toBe("body-01");
    expect(PRESETS.map((preset) => preset.preset_id)).toContain("analyst");

    state = setPaletteColor(state, 0, "#112233");
    expect(state.draft.palette[0]).toBe("#112233");
    state = setPaletteColor(state, 0, "not-a-colour");
    expect(state.draft.palette[0]).toBe("#112233");

    state = setShadowPolicy(state, "room");
    expect(state.draft.shadow_policy).toBe("room");

    state = moveLayer(state, "hair", "up");
    expect(state.draft.layer_order.indexOf("hair"))
      .toBe(state.draft.layer_order.indexOf("eyes") - 1);
    expect(new Set(state.draft.layer_order))
      .toEqual(new Set(LAYER_ORDER));

    state = setPortraitSize(state, 48);
    expect(state.portraitSize).toBe(48);
    expect(state.draft.portrait_size).toBe(48);

    state = setPreviewAnimation(state, "talk");
    expect(state.previewAnimation).toBe("talk");
  });

  it("describes the selection for a live region", () => {
    let state = createStudioState();
    expect(liveText(state)).toContain("category body");
    expect(liveText(state)).toContain("portrait 32");
    state = setPreviewAnimation(state, "walk");
    expect(liveText(state)).toContain("animation walk");
    expect(ANIMATIONS).toContain("walk");
  });

  it("keeps the documented part counts in one place", () => {
    expect(PART_COUNTS.body).toBe(9);
    expect(PART_COUNTS.hair).toBe(29);
  });
});

describe("one save call", () => {
  it("posts the canonical profile once and returns the saved revision", async () => {
    const { calls, impl } = recordingFetch((call) =>
      respond(200, {
        ok: true,
        profile: { ...call.body.profile, revision: 7 },
      }));
    const state = createStudioState({ profile_id: "profile-a" });
    const result = await saveCharacterProfile(state.draft, {
      base: "", fetchImpl: impl,
    });
    expect(result.kind).toBe("saved");
    if (result.kind === "saved") {
      expect(result.profile.revision).toBe(7);
      expect(result.profile.body_id).toBe("body-00");
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toContain("/character-profiles");
    expect(calls[0].body.profile.profile_id).toBe("profile-a");
    expect(calls[0].body.owner).toBe(true);
  });

  it("turns a typed refusal into an error, not a saved state", async () => {
    const { impl } = recordingFetch(() =>
      respond(403, {
        ok: false, code: "PAID_ART_REFUSED", detail: "paid path inside repo",
      }));
    const result = await saveCharacterProfile(createStudioState().draft, {
      fetchImpl: impl,
    });
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.code).toBe("PAID_ART_REFUSED");
      expect(result.detail).toContain("paid path");
    }
  });

  it("keeps a transport failure visible", async () => {
    const result = await saveCharacterProfile(createStudioState().draft, {
      fetchImpl: (() => { throw new Error("offline"); }) as never,
    });
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.code).toBe("BUSINESS_UNREACHABLE");
    }
  });
});
