/* P3-07 Character Studio: one canonical profile, one save call.
 *
 * The panel renders part pickers/search/counts, lock + randomize, undo/redo,
 * presets, layer/palette/shadow controls, world-scale and 32/48 portrait
 * previews, and the animation preview set. It never holds a second store and
 * never renders raw paid art: parts are labels/ids and previews are palette
 * swatches. A missing part is shown as a visible fallback notice; a failed save
 * is a visible typed error.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
type ReactLike = {
  createElement: (...args: unknown[]) => unknown;
};
type HooksLike = {
  useState: <T>(value: T) => [T, (next: T | ((prev: T) => T)) => void];
};
import { sakugaButton } from "./sakuga";
import {
  ANIMATIONS,
  type AnimationName,
  type CharacterProfile,
  type Fallback,
  type LayerName,
  PART_CATEGORIES,
  PART_CATALOG,
  type Part,
  type PartCategory,
  PRESETS,
  SHADOW_POLICIES,
  type SaveResult,
  type ShadowPolicy,
  type StudioState,
  applyPreset,
  createStudioState,
  liveText,
  moveLayer,
  partCounts,
  pickPart,
  projectProfile,
  randomize,
  redo,
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

export interface StudioRevision {
  revision: number;
  kind: string;
  current?: boolean;
}

export interface CharacterStudioProps {
  initialProfile?: Partial<CharacterProfile> | null;
  catalog?: Part[];
  base?: string;
  fetchImpl?: typeof fetch;
  /** Injected in tests; the default is the one Business OS save call. */
  saveProfile?: (profile: CharacterProfile) => Promise<SaveResult>;
  /** Revision history from the save authority; rollback is only offered here. */
  revisions?: StudioRevision[];
  rollbackProfile?: (
    profileId: string,
    revision: number,
  ) => Promise<SaveResult>;
}

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; revision: number }
  | { kind: "error"; code: string; detail: string };

interface KeyEventLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  preventDefault?: () => void;
}

const ALL_CATEGORIES: PartCategory[] = [...PART_CATEGORIES, "accessory"];

function labelOf(category: PartCategory): string {
  return category.charAt(0).toUpperCase() + category.slice(1);
}

export function createCharacterStudio(
  R: ReactLike,
  hooks: HooksLike,
) {
  const { useState } = hooks;

  return function CharacterStudio(props: CharacterStudioProps) {
    const catalog = props.catalog ?? PART_CATALOG;
    const [state, setState] = useState<StudioState>(
      createStudioState(props.initialProfile, catalog));
    const [save, setSave] = useState<SaveState>({ kind: "idle" });

    const update = (
      fn: (current: StudioState) => StudioState,
    ) => setState((current) => fn(current));

    const updateDraft = (fn: (current: StudioState) => StudioState) => {
      update(fn);
      setSave({ kind: "idle" });
    };

    const counts = partCounts(catalog);
    const parts = searchParts(state.selectedCategory, state.search, catalog);
    const palette = state.draft.palette;
    const projection = projectProfile(state.draft);
    const revisions = props.revisions ?? [];
    const swatch = `linear-gradient(135deg, ${palette[0] ?? "#1f2933"}, ${
      palette[1] ?? palette[0] ?? "#3e4c59"})`;

    const onSave = () => {
      setSave({ kind: "saving" });
      const saver = props.saveProfile
        ?? ((profile: CharacterProfile) => saveCharacterProfile(profile, {
          base: props.base,
          fetchImpl: props.fetchImpl,
        }));
      void saver(state.draft).then((result) => {
        if (result.kind === "saved") {
          setSave({ kind: "saved", revision: result.profile.revision });
        } else {
          setSave({
            kind: "error", code: result.code, detail: result.detail,
          });
        }
      }).catch((err: unknown) => {
        setSave({
          kind: "error",
          code: "CHARACTER_SAVE_FAILED",
          detail: err instanceof Error ? err.message : String(err),
        });
      });
    };

    const onRollback = (revision: number) => {
      setSave({ kind: "saving" });
      const roller = props.rollbackProfile
        ?? ((profileId: string, target: number) =>
          rollbackCharacterProfile(profileId, target, {
            base: props.base, fetchImpl: props.fetchImpl,
          }));
      void roller(state.draft.profile_id, revision).then((result) => {
        if (result.kind === "saved") {
          setState((current) => ({
            ...current,
            draft: result.profile,
            history: [...current.history, current.draft].slice(-50),
            future: [],
          }));
          setSave({ kind: "saved", revision: result.profile.revision });
        } else {
          setSave({
            kind: "error", code: result.code, detail: result.detail,
          });
        }
      }).catch((err: unknown) => {
        setSave({
          kind: "error",
          code: "CHARACTER_ROLLBACK_FAILED",
          detail: err instanceof Error ? err.message : String(err),
        });
      });
    };

    const onKeyDown = (event: KeyEventLike) => {
      const key = String(event.key || "").toLowerCase();
      const modifier = Boolean(event.ctrlKey || event.metaKey);
      if (modifier && key === "z") {
        event.preventDefault?.();
        update(event.shiftKey ? redo : undo);
      } else if (modifier && key === "y") {
        event.preventDefault?.();
        update(redo);
      }
    };

    const onPartKeyDown = (event: KeyEventLike) => {
      const key = event.key;
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(key)) return;
      if (!parts.length) return;
      event.preventDefault?.();
      const selected = state.selectedCategory === "accessory"
        ? ""
        : String(state.draft[
          `${state.selectedCategory}_id` as keyof CharacterProfile]);
      const index = Math.max(
        0, parts.findIndex((part) => part.part_id === selected));
      let next = index;
      if (key === "ArrowLeft") next = Math.max(0, index - 1);
      if (key === "ArrowRight") next = Math.min(parts.length - 1, index + 1);
      if (key === "Home") next = 0;
      if (key === "End") next = parts.length - 1;
      updateDraft((current) =>
        pickPart(current, current.selectedCategory, parts[next].part_id));
    };

    const fallbackNotice = (items: Fallback[]) => {
      if (!items.length) return null;
      return R.createElement("div", {
        key: "fallbacks",
        className: "character-studio__notice",
        "data-testid": "character-fallback",
        role: "status",
        "aria-live": "polite",
      }, `Missing parts replaced: ${items.map((row) =>
        `${row.category} ${row.from || "none"} → ${row.to || "removed"}`)
        .join("; ")}`);
    };

    const saveStatus = () => {
      if (save.kind === "saving") {
        return R.createElement("div", {
          key: "save-state",
          "data-testid": "character-save-status",
          "data-state": "saving",
          role: "status",
          "aria-live": "polite",
        }, "Saving the canonical profile…");
      }
      if (save.kind === "saved") {
        return R.createElement("div", {
          key: "save-state",
          "data-testid": "character-save-status",
          "data-state": "saved",
          role: "status",
          "aria-live": "polite",
        }, `Saved revision ${save.revision}.`);
      }
      return R.createElement("div", {
        key: "save-state",
        "data-testid": "character-save-status",
        "data-state": "idle",
        role: "status",
        "aria-live": "polite",
      }, "Not saved yet.");
    };

    const categoryButton = (category: PartCategory) => R.createElement(
      "button",
      {
        key: `cat-${category}`,
        type: "button",
        className: "character-studio__category",
        "data-testid": "character-category",
        "data-category": category,
        "aria-pressed": state.selectedCategory === category,
        "aria-label": `${labelOf(category)} parts, ${counts[category] ?? 0}`,
        onClick: () => update((current) =>
          selectCategory(current, category)),
      },
      `${labelOf(category)} (${counts[category] ?? 0})`,
    );

    const partButton = (part: Part) => {
      const selected = part.category === "accessory"
        ? state.draft.accessory_ids.includes(part.part_id)
        : String(state.draft[
          `${part.category}_id` as keyof CharacterProfile]) === part.part_id;
      return sakugaButton(R, {
        key: part.part_id,
        type: "button",
        className: "character-studio__part",
        "data-testid": "character-part",
        "data-part": part.part_id,
        "data-category": part.category,
        "aria-pressed": selected,
        "aria-label": `${part.label} (${part.part_id})`,
        onClick: () => updateDraft((current) =>
          pickPart(current, part.category, part.part_id)),
      }, part.label);
    };

    const lockButton = (category: PartCategory) => R.createElement(
      "button",
      {
        key: `lock-${category}`,
        type: "button",
        "data-testid": "character-lock",
        "data-category": category,
        "aria-pressed": state.locks.includes(category),
        "aria-label": `${state.locks.includes(category) ? "Unlock" : "Lock"}`
          + ` ${category}`,
        onClick: () => updateDraft((current) =>
          toggleLock(current, category)),
      },
      state.locks.includes(category) ? "Locked" : "Lock",
    );

    const layerRow = (layer: LayerName, index: number) => [
      R.createElement("li", {
        key: `layer-${layer}`,
        "data-testid": "character-layer-row",
        "data-layer": layer,
      }, layer),
      sakugaButton(R, {
        key: `layer-up-${layer}`,
        type: "button",
        "data-testid": "character-layer",
        "data-layer": layer,
        "data-action": "up",
        "aria-label": `Move ${layer} layer up`,
        disabled: index === 0,
        onClick: () => updateDraft((current) =>
          moveLayer(current, layer, "up")),
      }, "↑"),
      sakugaButton(R, {
        key: `layer-down-${layer}`,
        type: "button",
        "data-testid": "character-layer",
        "data-layer": layer,
        "data-action": "down",
        "aria-label": `Move ${layer} layer down`,
        disabled: index === state.draft.layer_order.length - 1,
        onClick: () => updateDraft((current) =>
          moveLayer(current, layer, "down")),
      }, "↓"),
    ];

    const paletteInput = (colour: string, index: number) => R.createElement(
      "input",
      {
        key: `palette-${index}`,
        type: "color",
        "data-testid": "character-palette",
        "data-index": index,
        "aria-label": `Palette colour ${index + 1}`,
        value: colour,
        onChange: (event: { target: { value: string } }) =>
          updateDraft((current) =>
            setPaletteColor(current, index, event.target.value)),
      },
    );

    const shadowButton = (policy: ShadowPolicy) => sakugaButton(R, {
      key: `shadow-${policy}`,
      type: "button",
      "data-testid": "character-shadow",
      "data-policy": policy,
      "aria-pressed": state.draft.shadow_policy === policy,
      "aria-label": `${policy} shadow policy`,
      onClick: () => updateDraft((current) =>
        setShadowPolicy(current, policy)),
    }, policy);

    const portraitButton = (size: 32 | 48) => sakugaButton(R, {
      key: `portrait-${size}`,
      type: "button",
      "data-testid": "character-portrait-size",
      "data-size": size,
      "aria-pressed": state.portraitSize === size,
      "aria-label": `${size} pixel portrait`,
      onClick: () => updateDraft((current) =>
        setPortraitSize(current, size)),
    }, `${size}px`);

    const animationButton = (animation: AnimationName) => R.createElement(
      "button",
      {
        key: `animation-${animation}`,
        type: "button",
        "data-testid": "character-animation",
        "data-animation": animation,
        "aria-pressed": state.previewAnimation === animation,
        "aria-label": `Preview ${animation} animation`,
        onClick: () => updateDraft((current) =>
          setPreviewAnimation(current, animation)),
      },
      animation,
    );

    const adapterCard = (adapter: string, label: string, detail: string) =>
      R.createElement("div", {
        key: `adapter-${adapter}`,
        "data-testid": "character-adapter",
        "data-adapter": adapter,
        role: "group",
        "aria-label": `${label} projection: ${detail}`,
      }, [
        R.createElement("strong", { key: "label" }, label),
        R.createElement("span", {
          key: "detail",
          "data-testid": "character-adapter-detail",
        }, detail),
      ]);

    const revisionRow = (row: StudioRevision) => sakugaButton(R, {
      key: `revision-${row.revision}`,
      type: "button",
      "data-testid": "character-rollback",
      "data-revision": row.revision,
      "aria-label": `Rollback to revision ${row.revision}`,
      disabled: Boolean(row.current) || save.kind === "saving",
      onClick: () => onRollback(row.revision),
    }, `r${row.revision} ${row.kind}`);

    const preview = (id: string, size: number, portrait: boolean) =>
      R.createElement("div", {
        key: id,
        className: "character-studio__preview",
        "data-testid": id,
        "data-animation": state.previewAnimation,
        "data-portrait-size": portrait ? state.portraitSize : undefined,
        "data-world-scale": portrait ? undefined : "1",
        "aria-label": `${portrait ? "Portrait" : "World-scale"} preview of`
          + ` ${state.draft.profile_id}`,
        style: {
          width: size,
          height: size,
          background: swatch,
          imageRendering: "pixelated",
        },
      });

    return R.createElement("section", {
      className: "character-studio",
      "data-testid": "character-studio",
      "aria-label": "Character Studio",
      onKeyDown,
    }, [
      R.createElement("header", { key: "head" }, [
        R.createElement("h2", { key: "title" }, "Character Studio"),
        R.createElement("p", {
          key: "live",
          "data-testid": "character-live",
          role: "status",
          "aria-live": "polite",
        }, liveText(state)),
        fallbackNotice(state.fallbacks),
      ]),

      R.createElement("div", { key: "toolbar", className: "character-studio__toolbar" }, [
        R.createElement("input", {
          key: "search",
          type: "search",
          value: state.search,
          placeholder: "Search parts",
          "data-testid": "character-search",
          "aria-label": "Search parts",
          onChange: (event: { target: { value: string } }) =>
            update((current) => setSearch(current, event.target.value)),
        }),
        sakugaButton(R, {
          key: "randomize",
          type: "button",
          "data-testid": "character-randomize",
          "aria-label": "Randomize unlocked parts",
          onClick: () => updateDraft((current) => randomize(current)),
        }, "Randomize"),
        sakugaButton(R, {
          key: "undo",
          type: "button",
          "data-testid": "character-undo",
          "aria-label": "Undo",
          disabled: state.history.length === 0,
          onClick: () => updateDraft(undo),
        }, "Undo"),
        sakugaButton(R, {
          key: "redo",
          type: "button",
          "data-testid": "character-redo",
          "aria-label": "Redo",
          disabled: state.future.length === 0,
          onClick: () => updateDraft(redo),
        }, "Redo"),
        ...PRESETS.map((preset) => sakugaButton(R, {
          key: `preset-${preset.preset_id}`,
          type: "button",
          "data-testid": "character-preset",
          "data-preset": preset.preset_id,
          "aria-label": `Apply ${preset.label} preset`,
          onClick: () => updateDraft((current) =>
            applyPreset(current, preset.preset_id, catalog)),
        }, preset.label)),
      ]),

      R.createElement("div", { key: "categories", role: "group",
        "aria-label": "Part categories" },
      ALL_CATEGORIES.map(categoryButton)),

      R.createElement("div", {
        key: "parts",
        role: "group",
        "data-testid": "character-parts",
        "aria-label": `${labelOf(state.selectedCategory)} parts`,
        onKeyDown: onPartKeyDown,
      }, parts.map(partButton)),

      R.createElement("div", { key: "locks", role: "group",
        "aria-label": "Part locks" }, ALL_CATEGORIES.map(lockButton)),

      R.createElement("ol", { key: "layers", "data-testid": "character-layers",
        "aria-label": "Layer order" },
      state.draft.layer_order.flatMap((layer: LayerName, index: number) =>
        layerRow(layer, index))),

      R.createElement("div", { key: "palette", role: "group",
        "aria-label": "Palette" }, palette.map(paletteInput)),

      R.createElement("div", { key: "shadow", role: "group",
        "aria-label": "Shadow policy" },
      SHADOW_POLICIES.map(shadowButton)),

      R.createElement("div", { key: "portrait", role: "group",
        "aria-label": "Portrait size" }, [32, 48].map((size) =>
        portraitButton(size as 32 | 48))),

      R.createElement("div", { key: "animations", role: "group",
        "aria-label": "Animation preview" },
      ANIMATIONS.map(animationButton)),

      R.createElement("div", { key: "previews", className: "character-studio__previews" }, [
        preview("character-world-preview", 96, false),
        preview("character-portrait-preview", state.portraitSize, true),
      ]),

      R.createElement("div", {
        key: "adapters",
        role: "group",
        "aria-label": "Adapter projections",
        "data-testid": "character-adapters",
      }, [
        adapterCard(
          "habblaud", "Habblaud",
          `${projection.adapters.habblaud.layers.length} layers; `
          + `shadow ${projection.adapters.habblaud.shadow_policy}; `
          + `${projection.adapters.habblaud.animation_set.length} animations`),
        adapterCard(
          "limezu_character", "LimeZu Character 2.0",
          `body ${projection.adapters.limezu_character.parts.body}; `
          + `eyes ${projection.adapters.limezu_character.parts.eyes}; `
          + `hair ${projection.adapters.limezu_character.parts.hair}; `
          + `outfit ${projection.adapters.limezu_character.parts.outfit}; `
          + `skin ${projection.adapters.limezu_character.parts.skin}`),
        adapterCard(
          "limezu_portrait", "LimeZu Portrait 1.5",
          `cell ${projection.adapters.limezu_portrait.cell_index}; `
          + `${projection.adapters.limezu_portrait.portrait_size}px; `
          + `${projection.adapters.limezu_portrait.frame}`),
      ]),

      revisions.length ? R.createElement("div", {
        key: "revisions",
        "data-testid": "character-revisions",
        role: "group",
        "aria-label": "Profile revisions",
      }, revisions.map(revisionRow)) : null,

      R.createElement("div", { key: "selection", "data-testid": "character-selection" },
        liveText(state)),
      saveStatus(),
      save.kind === "error" ? R.createElement("div", {
        key: "save-error",
        "data-testid": "character-error",
        className: "character-studio__error",
        role: "alert",
      }, `${save.code}: ${save.detail}`) : null,
      sakugaButton(R, {
        key: "save",
        type: "button",
        "data-testid": "character-save",
        "aria-label": "Save character profile",
        disabled: save.kind === "saving",
        onClick: onSave,
      }, save.kind === "saving" ? "Saving…" : "Save profile"),
    ]);
  };
}
