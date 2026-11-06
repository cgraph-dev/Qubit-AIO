/**
 * Visual vocabulary for the workbench, in one place.
 *
 * The surface this page absorbs is a dark three-region console; its CSS is a
 * set of literal values (`--bg:#151614; --sidebar:#242624; --surface:#1B1C1A;
 * --border:#343631; …` for the dark palette at
 * `static/style.css:156-161`). Qubit's app-level theme variables carry different
 * names and different light/dark values, so this module states the local
 * mapping once, here, rather than scattering hex values through components.
 *
 * Every value is a CSS custom property where the app already has one, so the
 * page follows the operator's theme where it can and falls back to the
 * absorbed palette where the app has no equivalent token.
 */

/** Region chrome strings. `cn`-merged by the components that use them. */
export const REGION = {
  /** 48px icon-only rail, same width the absorbed console used. */
  rail: "flex w-12 shrink-0 flex-col items-center gap-1 bg-background-base/60 py-2",
  /** Session sidebar column. */
  sidebar: "flex w-[19rem] shrink-0 flex-col border-r border-border bg-background-base/40",
  /** Centre chat column. */
  chat: "flex min-h-0 min-w-0 flex-1 flex-col bg-background-base/20",
  /** Right workspace column. */
  workspace: "flex w-[20rem] shrink-0 flex-col border-l border-border bg-background-base/40",
  /** One row of a panel header: 41px min height, 11px uppercase 600 label. */
  panelHead:
    "flex min-h-[2.5rem] shrink-0 items-center justify-between gap-2 border-b border-border px-3",
} as const;

/** The uppercase micro-label used by every panel header. */
export const PANEL_HEAD_LABEL =
  "truncate text-[11px] font-semibold uppercase tracking-[0.08em] text-text-tertiary";

/** 24×24 square icon button used in panel headers. */
export const HEAD_ICON_BUTTON =
  "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-secondary/50 hover:text-foreground disabled:pointer-events-none disabled:opacity-35 [&>svg]:h-3.5 [&>svg]:w-3.5";

/** 36×36 rail button; `active` adds the accent bar the console drew. */
export const RAIL_BUTTON =
  "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors [&>svg]:h-[18px] [&>svg]:w-[18px]";

export const RAIL_BUTTON_IDLE =
  "text-text-tertiary hover:bg-secondary/50 hover:text-foreground";

export const RAIL_BUTTON_ACTIVE =
  "bg-primary/15 text-primary before:absolute before:-left-1.5 before:top-1/2 before:h-4 before:w-[3px] before:-translate-y-1/2 before:rounded-r-sm before:bg-primary";

export const RAIL_BUTTON_DISABLED = "cursor-not-allowed text-text-tertiary/40";

/**
 * Per-source badge colours. The keys are the store's REAL `source` values
 * (`tui`, `cron`, `voice`, …), read from `GET /api/workbench/sessions` rows on
 * this backend — not a guess at another product's channel list. An unknown
 * source renders the neutral badge with its own name; it is never recoloured
 * into a channel this store does not have.
 */
export const SOURCE_TONE: Record<string, string> = {
  tui: "text-sky-300 bg-sky-500/15",
  cron: "text-amber-300 bg-amber-500/15",
  voice: "text-fuchsia-300 bg-fuchsia-500/15",
  api: "text-teal-300 bg-teal-500/15",
  cli: "text-emerald-300 bg-emerald-500/15",
};

export const SOURCE_TONE_FALLBACK = "text-text-secondary bg-secondary/60";

/** Badge pill shared by the sidebar source chip and the artifact icon tone. */
export const SOURCE_BADGE =
  "shrink-0 rounded-[3px] px-1 py-px text-[9px] font-semibold uppercase leading-[1.4] tracking-[0.04em]";

/** Assistant/user avatar discs in the transcript. */
export const ROLE_DISC =
  "flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full border text-[10px] font-bold";
