/* F08 — settings: every one is either LOCAL or explicitly SCOPED.
 *
 * THE RULE THIS FILE ENFORCES
 * ---------------------------
 * F08's proof: "settings change only local view controls unless a named scoped
 * backend action is confirmed."
 *
 * The dangerous failure is a setting that LOOKS like a preference and is
 * actually an org mutation — moving a desk that grants a tool, muting a channel
 * that suppresses an audit. So every setting declares its `scope`, and the
 * store REFUSES to apply a `backend`-scoped setting through the local path. A
 * caller cannot forget: the type system and the runtime both stop it.
 *
 * Nothing here talks to the network. A scoped setting names the backend action
 * it needs; applying it is a separate, confirmed step (F13's staged commands).
 */

/** Where a setting lives. Deliberately only three values. */
export type SettingScope =
  /** This browser, this player. Never leaves the device. */
  | "local"
  /** Local presentation, but it changes what the WORLD shows (camera, filters). */
  | "view"
  /** Changes org state. Requires a confirmed, named backend action. */
  | "backend";

export interface SettingSpec<T> {
  id: string;
  label: string;
  /** Shown next to the control. One line, plain language. */
  help: string;
  scope: SettingScope;
  default: T;
  /** For a scoped setting: the backend action it needs when applied. */
  backendAction?: string;
  /** Allowed values, when the setting is an enumeration. */
  options?: readonly { value: T; label: string }[];
}

/** The settings the shell owns. Ids are stable — they are storage keys. */
export const SETTINGS = {
  /** Text zoom. LOCAL: it scales the UI, nothing else. */
  textScale: {
    id: "office.textScale",
    label: "Text size",
    help: "Scales the interface text only. The world is unaffected.",
    scope: "local",
    default: 1,
    options: [
      { value: 0.9, label: "Small" },
      { value: 1, label: "Normal" },
      { value: 1.15, label: "Large" },
      { value: 1.3, label: "Larger" },
    ],
  } as SettingSpec<number>,

  /** Reduced motion. LOCAL: zeroes the shell's transitions. */
  reducedMotion: {
    id: "office.reducedMotion",
    label: "Reduce motion",
    help: "Removes interface transitions. Your system setting is always honoured too.",
    scope: "local",
    default: false,
    options: [
      { value: false, label: "Off" },
      { value: true, label: "On" },
    ],
  } as SettingSpec<boolean>,

  /** High contrast. LOCAL: swaps tokens, never filters the scene. */
  highContrast: {
    id: "office.highContrast",
    label: "High contrast",
    help: "Stronger text and borders.",
    scope: "local",
    default: false,
    options: [
      { value: false, label: "Off" },
      { value: true, label: "On" },
    ],
  } as SettingSpec<boolean>,

  /** What the world draws. VIEW: presentation, no org mutation. */
  showLabels: {
    id: "office.showLabels",
    label: "Character labels",
    help: "Draw names over the figures. Presentation only.",
    scope: "view",
    default: true,
    options: [
      { value: true, label: "Shown" },
      { value: false, label: "Hidden" },
    ],
  } as SettingSpec<boolean>,

  /**
   * !! THE DANGEROUS ONE, AND DELIBERATELY PRESENT !!
   *
   * "Floor layout" reads like a view preference and is NOT: a room's objects
   * grant its occupants' tools (C12), so rearranging a floor changes
   * capabilities. It is `backend` scope, and the local store refuses it. It is
   * here so the refusal is exercised by a real setting rather than asserted in
   * the abstract.
   */
  floorLayout: {
    id: "office.floorLayout",
    label: "Floor layout",
    help: "Rearranging a room changes which tools its occupants may use, so " +
          "this is an organisation change, not a display preference.",
    scope: "backend",
    backendAction: "office.floor.update",
    default: "unchanged",
    options: [
      { value: "unchanged", label: "Leave as reported" },
    ],
  } as SettingSpec<string>,
} as const;

export type SettingValues = {
  [K in keyof typeof SETTINGS]: (typeof SETTINGS)[K] extends SettingSpec<infer T>
    ? T
    : never;
};

export function defaultValues(): SettingValues {
  const out = {} as Record<string, unknown>;
  for (const [key, spec] of Object.entries(SETTINGS)) {
    out[key] = (spec as SettingSpec<unknown>).default;
  }
  return out as SettingValues;
}

/** Apply an edit. Returns the next values and REFUSES a backend-scoped one. */
export function applySetting<K extends keyof SettingValues>(
  values: SettingValues,
  key: K,
  value: SettingValues[K],
): { ok: true; values: SettingValues } | { ok: false; reason: string } {
  const spec = SETTINGS[key] as SettingSpec<unknown> | undefined;
  if (!spec) return { ok: false, reason: `${String(key)} is not a setting` };
  if (spec.scope === "backend") {
    // THE REFUSAL THAT MATTERS. Not a warning, not a confirm — a local store
    // may not apply this at all. It needs the named backend action.
    return {
      ok: false,
      reason:
        `"${spec.label}" changes organisation state, not this view. It needs ` +
        `the confirmed action ${spec.backendAction ?? "(unnamed)"}; a display ` +
        `preference cannot apply it.`,
    };
  }
  if (spec.options && !spec.options.some((o) => o.value === value)) {
    return {
      ok: false,
      reason: `${String(value)} is not an allowed value for ${spec.label}`,
    };
  }
  return { ok: true, values: { ...values, [key]: value } };
}

/** True when a stored edit differs from what is applied (the dirty state). */
export function isDirty(values: SettingValues, draft: SettingValues): boolean {
  return (Object.keys(SETTINGS) as Array<keyof SettingValues>).some(
    (k) => values[k] !== draft[k],
  );
}

/** The ids that are safe to persist to localStorage. Backend ones never are. */
export function persistableIds(): string[] {
  return Object.values(SETTINGS)
    .filter((s) => (s as SettingSpec<unknown>).scope !== "backend")
    .map((s) => s.id);
}

/** Read stored values, ignoring anything that is not a safe local setting. */
export function loadStored(get: (id: string) => string | null): SettingValues {
  const out = defaultValues() as Record<string, unknown>;
  for (const [key, spec] of Object.entries(SETTINGS)) {
    const s = spec as SettingSpec<unknown>;
    if (s.scope === "backend") continue;   // never restorable from the client
    const raw = get(s.id);
    if (raw === null) continue;
    const option = s.options?.find((o) => JSON.stringify(o.value) === raw);
    if (option) out[key] = option.value;
  }
  return out as SettingValues;
}

/** Write only the safe settings. */
export function storeValues(
  values: SettingValues,
  set: (id: string, raw: string) => void,
): void {
  for (const [key, spec] of Object.entries(SETTINGS)) {
    const s = spec as SettingSpec<unknown>;
    if (s.scope === "backend") continue;
    set(s.id, JSON.stringify(values[key as keyof SettingValues]));
  }
}

/** The DOM attributes the shell root carries, derived from the values. */
export function shellAttributes(values: SettingValues): Record<string, string> {
  return {
    "data-office-reduced-motion": values.reducedMotion ? "1" : "0",
    "data-office-contrast": values.highContrast ? "high" : "normal",
    "data-office-scale": String(values.textScale),
  };
}
