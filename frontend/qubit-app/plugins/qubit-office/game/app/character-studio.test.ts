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
  projectHabblaud,
  projectLimezuCharacter,
  projectLimezuPortrait,
  projectProfile,
  randomize,
  redo,
  resolveProfile,
  rollbackCharacterProfile,
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
    expect(serialised).not.toContain("/home/example/paid-art");
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

describe("three adapter projections", () => {
  it("projects one canonical profile into all three adapters deterministically", () => {
    const state = createStudioState({
      profile_id: "profile-a", agent_id: "agent-a", revision: 4,
    });
    const first = projectProfile(state.draft);
    const second = projectProfile(state.draft);
    expect(first).toEqual(second);
    expect(first.schema).toBe("qubit-office-character-projection/1");
    expect(Object.keys(first.adapters).sort()).toEqual([
      "habblaud", "limezu_character", "limezu_portrait"]);
    expect(first.revision).toBe(4);
    expect(first.profile_id).toBe("profile-a");
  });

  it("maps Habblaud layers in the profile's declared order", () => {
    const state = createStudioState({
      layer_order: ["body", "outfit", "skin", "hair", "eyes", "accessory"],
      accessory_ids: ["accessory-01", "accessory-03"],
    });
    const projection = projectHabblaud(state.draft);
    expect(projection.layers.map((row) => row.layer)).toEqual([
      "body", "outfit", "skin", "hair", "eyes", "accessory"]);
    expect(projection.layers[0].part_id).toBe("body-00");
    const accessory = projection.layers[5];
    expect(accessory.part_ids).toEqual(["accessory-01", "accessory-03"]);
    expect(accessory.indices).toEqual([1, 3]);
    expect(projection.shadow_policy).toBe("sprite");
  });

  it("maps Character Generator 2.0 part indices", () => {
    const state = createStudioState({
      body_id: "body-03", eyes_id: "eyes-05",
      hair_id: "hair-27", outfit_id: "outfit-30", skin_id: "skin-08",
    });
    const projection = projectLimezuCharacter(state.draft);
    expect(projection.adapter).toBe("limezu-character-generator-2.0");
    expect(projection.parts).toEqual({
      body: 3, skin: 8, eyes: 5, hair: 27, outfit: 30,
    });
    expect(projection.part_ids.body).toBe("body-03");
  });

  it("maps Portrait Generator 1.5 cell and size", () => {
    let state = createStudioState();
    expect(projectLimezuPortrait(state.draft).cell_index).toBe(0);
    state = setPortraitSize(state, 48);
    const projection = projectLimezuPortrait(state.draft);
    expect(projection.portrait_size).toBe(48);
    expect(projection.frame).toBe("cell-0_48");
  });

  it("never carries a local or paid asset path", () => {
    const serialised = JSON.stringify(projectProfile(createStudioState().draft));
    expect(serialised).not.toContain("/home/example/paid-art");
    expect(serialised).not.toContain(".png");
    expect(serialised).not.toContain("source_asset_refs");
    expect(serialised).not.toContain("local_path");
  });
});

describe("rollback over the one save authority", () => {
  it("posts the target revision once and returns the appended revision", async () => {
    const { calls, impl } = recordingFetch((call) =>
      respond(200, {
        ok: true,
        profile: { ...call.body.profile, revision: 5 },
      }));
    const saved = await rollbackCharacterProfile("profile-a", 1, {
      fetchImpl: impl,
    });
    expect(saved.kind).toBe("saved");
    if (saved.kind === "saved") expect(saved.profile.revision).toBe(5);
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toContain("/character-profiles/profile-a/rollback");
    expect(calls[0].body.revision).toBe(1);
  });

  it("turns a missing revision into a visible typed error", async () => {
    const { impl } = recordingFetch(() =>
      respond(404, {
        ok: false, code: "CHARACTER_REVISION_NOT_FOUND", detail: "no r9",
      }));
    const result = await rollbackCharacterProfile("profile-a", 9, {
      fetchImpl: impl,
    });
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.code).toBe("CHARACTER_REVISION_NOT_FOUND");
    }
  });
});
