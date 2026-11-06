/**
 * Pure helpers behind `ContextRing`.
 *
 * Kept out of the component file so the component module exports only a
 * component (Fast Refresh) and so the arithmetic can be imported and tested
 * without a DOM.
 *
 * The honesty rule is the load-bearing part, carried over from the absorbed
 * upstream (hermes-webui `static/ui.js:9940-9972`): the percentage is
 * `prompt_tokens / context_window` for the LAST prompt only. It is never
 * derived from cumulative input tokens — those sum across every turn, so
 * dividing them by the window produces a nonsense figure ("890% used") on any
 * long session.
 */

export type ContextPressure = "calm" | "warm" | "hot";

/** Pressure band for a percentage. Exported so the tooltip and the stroke
 *  colour can never disagree. */
export function contextPressure(percent: number): ContextPressure {
  if (percent > 85) return "hot";
  if (percent > 50) return "warm";
  return "calm";
}

export function fmtContextTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

/** The ring's accessible label. Pure, so a test can pin the exact wording. */
export function contextRingLabel(
  percent: number | null,
  usedTokens?: number | null,
  windowTokens?: number | null,
): string {
  if (percent == null) {
    return usedTokens != null && usedTokens > 0
      ? `Context: ${fmtContextTokens(usedTokens)} tokens used, window unknown`
      : "Context: no measurement yet";
  }
  const shown = Math.min(100, Math.round(percent));
  return (
    `Context window ${shown}% used (${100 - shown}% left) · ` +
    `${fmtContextTokens(usedTokens)} / ${fmtContextTokens(windowTokens)} tokens`
  );
}

/**
 * Turn a receipt's prompt measurement into the ring's percentage.
 *
 * Returns `null` — not 0 — when either side is missing, because 0 would read
 * as an empty context when the truth is "unknown".
 */
export function contextPercent(
  promptTokens: number | null | undefined,
  windowTokens: number | null | undefined,
): number | null {
  if (promptTokens == null || windowTokens == null) return null;
  if (!Number.isFinite(promptTokens) || !Number.isFinite(windowTokens)) return null;
  if (promptTokens <= 0 || windowTokens <= 0) return null;
  return (promptTokens / windowTokens) * 100;
}
