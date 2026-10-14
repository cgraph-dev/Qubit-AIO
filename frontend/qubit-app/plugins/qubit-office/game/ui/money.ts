/* F12 — money, from INTEGER CENTS, and the honesty rules around it.
 *
 * THREE LIES THIS FILE EXISTS TO PREVENT
 * --------------------------------------
 * 1. **A missing number rendered as zero.** `hud.runway_days` is nullable by
 *    design (`tower.py`: `runway = net / burn if burn > 0 else None`). Rendering
 *    that `null` as `0` reads as "bankrupt today" — a figure the backend never
 *    said and the exact opposite of "nothing is burning yet". `null` is not
 *    zero, and `Infinity` is not a runway.
 * 2. **A float formatted through the integer formatter.** Money in the tower is
 *    INTEGER CENTS. `burn_cents_per_day` is a different animal — a float rate
 *    that can be sub-cent (`0.4286`). Running it through the cents formatter
 *    prints `$0.00.4286`. It gets its own formatter.
 * 3. **A stale fold shown as a confident one.** Every money figure here is a
 *    fold the backend computed at some instant. If that instant is older than
 *    the freshness window, the panel must SAY SO rather than print a number the
 *    reader will trust as current.
 *
 * This module is pure and has no DOM, no React, no clock of its own: `now` is
 * always passed in, because a hidden second clock is how a renderer invents
 * state (C7).
 */

/** Cents -> a dollar string, from the INTEGER. No float round-trip. */
export function money(cents: number | null | undefined): MoneyText {
  if (cents === null || cents === undefined) {
    // "unknown" is spelled the same everywhere so a caller can match on it.
    return { text: "unknown", known: false, cents: null };
  }
  if (!Number.isFinite(cents)) {
    // Infinity/NaN are not amounts. Saying so is the only honest option.
    return { text: "unknown", known: false, cents: null };
  }
  // The sign is carried separately so `-$4.20`, never `$-4.20`.
  const neg = cents < 0;
  const abs = Math.abs(Math.round(cents));      // integer cents, rounded once
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;
  const body = `$${whole}.${String(frac).padStart(2, "0")}`;
  return {
    text: neg ? `-${body}` : body,
    known: true,
    cents: neg ? -abs : abs,
  };
}

export interface MoneyText {
  /** What to render. `"unknown"` when the payload gave nothing usable. */
  text: string;
  /** False for a null / non-finite amount. A caller must not treat as 0. */
  known: boolean;
  /** The integer cents, or null. Never a float. */
  cents: number | null;
}

/**
 * A per-day RATE (float cents). Deliberately a separate function: passing this
 * through `money()` is the `$0.00.4286` bug.
 */
export function moneyRate(centsPerDay: number | null | undefined): MoneyText {
  if (centsPerDay === null || centsPerDay === undefined
      || !Number.isFinite(centsPerDay)) {
    return { text: "unknown", known: false, cents: null };
  }
  const rounded = Math.round(centsPerDay * 100) / 100;   // two decimal places
  const whole = Math.floor(Math.abs(rounded) / 100);
  const frac = Math.round(Math.abs(rounded) % 100);
  const neg = rounded < 0;
  const body = `$${whole}.${String(frac).padStart(2, "0")}/day`;
  return { text: neg ? `-${body}` : body, known: true, cents: centsPerDay };
}

export interface RunwayView {
  /** `"known"` only when the number is actually derived. */
  state: "known" | "unknown";
  text: string;
  hint: string;
  days: number | null;
}

/**
 * The runway, with the one rule that matters: `null` is UNKNOWN, not zero.
 *
 * `runway_is_derived` is the backend's own statement of whether `runway_days`
 * was computed (`tower.py` sets it `runway is not None`). When it is false the
 * number is NOT a runway and must not be shown as one, whatever `runway_days`
 * happens to contain — a `0` there means "not derived", not "bankrupt".
 */
export function runwayView(
  days: number | null | undefined,
  isDerived: boolean,
): RunwayView {
  const derived = isDerived === true && days !== null && days !== undefined
    && Number.isFinite(days);
  if (!derived) {
    return {
      state: "unknown",
      text: "unknown",
      // The backend's reason, in the backend's own words.
      hint: "nothing is burning yet",
      days: null,
    };
  }
  return {
    state: "known",
    text: `${Math.round(days as number)} ${Math.round(days as number) === 1
      ? "day" : "days"}`,
    hint: "derived from the current burn and balance",
    days: days as number,
  };
}

export interface Staleness {
  stale: boolean;
  /** Seconds since the fold, or null when there is no timestamp. */
  ageS: number | null;
  /** The window it was judged against, echoed so the UI can name it. */
  windowS: number;
  reason: string;
}

/**
 * Is a fold older than the freshness window?
 *
 * `as_of` is when the backend computed the figure; `now` is the payload's own
 * clock; `windowS` is `active_window_s`. A figure we cannot date is treated as
 * STALE — "I don't know how old this is" is not "this is current".
 */
export function staleness(
  asOf: number | null | undefined,
  now: number | null | undefined,
  windowS: number,
): Staleness {
  const win = Number.isFinite(windowS) && windowS > 0 ? windowS : 0;
  if (asOf === null || asOf === undefined || !Number.isFinite(asOf)
      || now === null || now === undefined || !Number.isFinite(now)) {
    return {
      stale: true,
      ageS: null,
      windowS: win,
      reason: "undated fold — treated as stale",
    };
  }
  const age = Math.max(0, now - asOf);
  const isStale = win <= 0 ? true : age > win;
  return {
    stale: isStale,
    ageS: age,
    windowS: win,
    reason: isStale
      ? `last folded ${Math.round(age)}s ago, older than the ${Math.round(win)}s window`
      : `folded ${Math.round(age)}s ago`,
  };
}

/**
 * A money figure that remembers where it came from, so a panel can render it
 * WITH its provenance and staleness rather than as a bare number.
 */
export interface MoneyFold {
  label: string;
  amount: MoneyText;
  asOf: number | null;
  staleness: Staleness;
  /** The backend sentence naming which fold produced this (never paraphrased). */
  source: string;
}

/** Build a labelled, provenance-carrying fold from raw cents. */
export function foldFromCents(
  label: string,
  cents: number | null | undefined,
  opts: { asOf?: number | null; now?: number | null; windowS?: number;
          source?: string } = {},
): MoneyFold {
  const now = opts.now ?? null;
  const asOf = opts.asOf ?? null;
  return {
    label,
    amount: money(cents),
    asOf,
    staleness: staleness(asOf, now, opts.windowS ?? 0),
    source: opts.source ?? "",
  };
}
