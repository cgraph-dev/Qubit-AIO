import { describe, expect, it } from "vitest";
import { createCharacterStudio } from "./CharacterStudio";
import type { CharacterProfile, SaveResult } from "./character-studio";
import {
  allByTestId,
  createHarness,
  textOf,
} from "./business-test-harness";

type Harness = ReturnType<typeof createHarness>;
type Element = Harness["tree"][number];

function mount(props: Record<string, unknown> = {}): Harness {
  const harness = createHarness();
  const Studio = createCharacterStudio(
    harness.React as never, harness.hooks as never);
  harness.mount(Studio as never, props);
  return harness;
}

function find(h: Harness, testId: string, match: (el: Element) => boolean) {
  const found = allByTestId(h, testId).find(match);
  if (!found) throw new Error(`missing ${testId}`);
  return found;
}

function click(h: Harness, testId: string, match: (el: Element) => boolean) {
  const button = find(h, testId, match);
  (button.props.onClick as () => void)();
  h.flush();
}

function live(h: Harness): string {
  return textOf(allByTestId(h, "character-live")[0]);
}

describe("CharacterStudio", () => {
  it("renders category counts, the part list and both previews", () => {
    const h = mount();
    const categories = allByTestId(h, "character-category");
    expect(textOf(categories.find(
      (el) => el.props["data-category"] === "hair"))).toContain("(29)");
    expect(textOf(categories.find(
      (el) => el.props["data-category"] === "body"))).toContain("(9)");
    expect(allByTestId(h, "character-part").length).toBe(9);
    expect(allByTestId(h, "character-world-preview")[0]
      .props["data-world-scale"]).toBe("1");
    expect(allByTestId(h, "character-portrait-preview")[0]
      .props["data-portrait-size"]).toBe(32);
    expect(live(h)).toContain("category body");
  });

  it("picks parts and updates the live text", () => {
    const h = mount();
    click(h, "character-category",
      (el) => el.props["data-category"] === "hair");
    expect(allByTestId(h, "character-part").length).toBe(29);
    click(h, "character-part",
      (el) => el.props["data-part"] === "hair-05");
    expect(live(h)).toContain("Hair 06");
    const selected = find(h, "character-part",
      (el) => el.props["data-part"] === "hair-05");
    expect(selected.props["aria-pressed"]).toBe(true);
  });

  it("searches within the selected category", () => {
    const h = mount();
    const search = allByTestId(h, "character-search")[0];
    (search.props.onChange as (event: unknown) => void)(
      { target: { value: "Body 03" } });
    h.flush();
    const parts = allByTestId(h, "character-part");
    expect(parts).toHaveLength(1);
    expect(parts[0].props["data-part"]).toBe("body-02");
  });

  it("locks a category and randomize leaves it alone", () => {
    const h = mount();
    click(h, "character-lock",
      (el) => el.props["data-category"] === "body");
    expect(live(h)).toContain("Body 01");
    click(h, "character-randomize", () => true);
    expect(live(h)).toContain("Body 01");
    expect(find(h, "character-lock",
      (el) => el.props["data-category"] === "body")
      .props["aria-pressed"]).toBe(true);
  });

  it("undoes and redoes through the toolbar", () => {
    const h = mount();
    click(h, "character-category",
      (el) => el.props["data-category"] === "hair");
    click(h, "character-part",
      (el) => el.props["data-part"] === "hair-01");
    expect(live(h)).toContain("Hair 02");
    click(h, "character-undo", () => true);
    expect(live(h)).toContain("Hair 01");
    click(h, "character-redo", () => true);
    expect(live(h)).toContain("Hair 02");
  });

  it("supports Ctrl+Z / Ctrl+Shift+Z through the keyboard path", () => {
    const h = mount();
    click(h, "character-category",
      (el) => el.props["data-category"] === "hair");
    click(h, "character-part",
      (el) => el.props["data-part"] === "hair-01");
    const root = allByTestId(h, "character-studio")[0];
    (root.props.onKeyDown as (event: unknown) => void)(
      { key: "z", ctrlKey: true, preventDefault: () => undefined });
    h.flush();
    expect(live(h)).toContain("Hair 01");
    (root.props.onKeyDown as (event: unknown) => void)(
      { key: "z", ctrlKey: true, shiftKey: true,
        preventDefault: () => undefined });
    h.flush();
    expect(live(h)).toContain("Hair 02");
  });

  it("moves the part selection with arrow keys", () => {
    const h = mount();
    const container = allByTestId(h, "character-parts")[0];
    (container.props.onKeyDown as (event: unknown) => void)(
      { key: "ArrowRight", preventDefault: () => undefined });
    h.flush();
    expect(live(h)).toContain("Body 02");
  });

  it("applies presets, layers, palette, shadow, portrait and animation", () => {
    const h = mount();
    click(h, "character-preset",
      (el) => el.props["data-preset"] === "analyst");
    expect(live(h)).toContain("Body 02");

    const rows = allByTestId(h, "character-layer-row");
    expect(rows.map((row) => row.props["data-layer"])).toEqual([
      "body", "outfit", "skin", "eyes", "hair", "accessory"]);
    click(h, "character-layer",
      (el) => el.props["data-layer"] === "hair"
        && el.props["data-action"] === "up");
    expect(allByTestId(h, "character-layer-row")
      .map((row) => row.props["data-layer"])).toEqual([
      "body", "outfit", "skin", "hair", "eyes", "accessory"]);

    const palette = allByTestId(h, "character-palette")[0];
    (palette.props.onChange as (event: unknown) => void)(
      { target: { value: "#112233" } });
    h.flush();
    expect(allByTestId(h, "character-palette")[0].props.value)
      .toBe("#112233");

    click(h, "character-shadow",
      (el) => el.props["data-policy"] === "room");
    click(h, "character-portrait-size",
      (el) => el.props["data-size"] === 48);
    expect(allByTestId(h, "character-portrait-preview")[0]
      .props["data-portrait-size"]).toBe(48);

    click(h, "character-animation",
      (el) => el.props["data-animation"] === "talk");
    expect(allByTestId(h, "character-world-preview")[0]
      .props["data-animation"]).toBe("talk");
  });

  it("shows a fallback notice for a missing part", () => {
    const h = mount({ initialProfile: { body_id: "body-999" } });
    const notice = allByTestId(h, "character-fallback")[0];
    expect(textOf(notice)).toContain("body");
    expect(live(h)).toContain("Body 01");
  });

  it("calls exactly one injected save and shows the saved revision", async () => {
    let calls = 0;
    const saver = async (profile: CharacterProfile)
      : Promise<SaveResult> => {
      calls += 1;
      return { kind: "saved", profile: { ...profile, revision: 7 } };
    };
    const h = mount({ saveProfile: saver });
    click(h, "character-save", () => true);
    await h.settle();
    expect(calls).toBe(1);
    expect(allByTestId(h, "character-save-status")[0]
      .props["data-state"]).toBe("saved");
    expect(textOf(allByTestId(h, "character-save-status")[0]))
      .toContain("Saved revision 7");
  });

  it("renders a visible typed error when the save is refused", async () => {
    const saver = async (): Promise<SaveResult> => ({
      kind: "error",
      code: "PAID_ART_REFUSED",
      detail: "paid art stays local",
    });
    const h = mount({ saveProfile: saver });
    click(h, "character-save", () => true);
    await h.settle();
    const error = allByTestId(h, "character-error")[0];
    expect(error.props.role).toBe("alert");
    expect(textOf(error)).toContain("PAID_ART_REFUSED");
    expect(textOf(error)).toContain("paid art stays local");
  });

  it("is keyboard/label accessible and exposes no paid asset path", () => {
    const h = mount();
    const interactive = h.tree.filter(
      (el) => el.type === "button" || el.type === "input"
        || el.props.role === "button");
    expect(interactive.length).toBeGreaterThan(20);
    for (const el of interactive) {
      expect(String(el.props["aria-label"] || "")).not.toBe("");
    }
    const liveRegion = allByTestId(h, "character-live")[0];
    expect(liveRegion.props.role).toBe("status");
    expect(liveRegion.props["aria-live"]).toBe("polite");
    expect(allByTestId(h, "character-studio")[0]
      .props["aria-label"]).toBe("Character Studio");

    const serialised = JSON.stringify(h.tree);
    expect(serialised).not.toContain("/home/example/paid-art");
    expect(serialised).not.toContain(".png");
  });
});

describe("CharacterStudio adapters and revisions", () => {
  it("renders the three adapter projections without any asset path", () => {
    const h = mount();
    const cards = allByTestId(h, "character-adapter");
    expect(cards.map((card) => card.props["data-adapter"])).toEqual([
      "habblaud", "limezu_character", "limezu_portrait"]);
    const details = allByTestId(h, "character-adapter-detail");
    expect(textOf(details[0])).toContain("layers");
    expect(textOf(details[1])).toContain("body 0");
    expect(textOf(details[2])).toContain("cell 0");
    expect(allByTestId(h, "character-adapters")[0].props.role).toBe("group");
    const serialised = JSON.stringify(h.tree);
    expect(serialised).not.toContain("/home/example/paid-art");
    expect(serialised).not.toContain(".png");
  });

  it("offers rollback only when the authority supplies revisions", async () => {
    let rolled: { profileId: string; revision: number } | null = null;
    const h = mount({
      revisions: [
        { revision: 1, kind: "save" },
        { revision: 2, kind: "save", current: true },
      ],
      rollbackProfile: async (profileId: string, revision: number) => {
        rolled = { profileId, revision };
        return {
          kind: "saved",
          profile: {
            profile_id: profileId, agent_id: "agent-draft",
            body_id: "body-00", skin_id: "skin-00", eyes_id: "eyes-00",
            hair_id: "hair-00", outfit_id: "outfit-00",
            accessory_ids: [], palette: ["#1f2933", "#3e4c59", "#f0b429", "#7fb3d5"],
            layer_order: ["body", "outfit", "skin", "eyes", "hair", "accessory"],
            shadow_policy: "sprite", portrait_cell: "cell-0",
            portrait_size: 32,
            animation_set: ["idle", "walk", "type", "talk", "think", "break", "celebrate"],
            style_version: 1, seed: 0, revision: 3,
          },
        };
      },
    });
    const buttons = allByTestId(h, "character-rollback");
    expect(buttons).toHaveLength(2);
    expect(buttons.find((b) => b.props["data-revision"] === 2)
      ?.props.disabled).toBe(true);
    click(h, "character-rollback", (el) => el.props["data-revision"] === 1);
    await h.settle();
    expect(rolled).toEqual({ profileId: "profile-draft", revision: 1 });
    expect(allByTestId(h, "character-save-status")[0]
      .props["data-state"]).toBe("saved");
    expect(textOf(allByTestId(h, "character-save-status")[0]))
      .toContain("Saved revision 3");
  });

  it("shows no revision controls by default", () => {
    const h = mount();
    expect(allByTestId(h, "character-revisions")).toHaveLength(0);
    expect(allByTestId(h, "character-rollback")).toHaveLength(0);
  });
});
