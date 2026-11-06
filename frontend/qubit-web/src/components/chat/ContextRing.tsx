/**
 * ContextRing — how full the model's context window is, as a small ring.
 *
 * Presentation only. It fetches nothing, owns no timer and never touches the
 * session store: every number arrives as a prop from the ONE page that already
 * holds it (`/qubit-chat` reads `effective_context_length` from
 * `getModelInfo` and `prompt_tokens` from the turn's `done` receipt).
 *
 * The honesty rule is the load-bearing part, carried over from the absorbed
 * upstream (hermes-webui `static/ui.js:9940-9972`): the percentage is
 * `prompt_tokens / context_window` for the LAST prompt only. It is never
 * derived from cumulative input tokens — those sum across every turn, so
 * dividing them by the window produces a nonsense figure ("890% used") on any
 * long session. When there is no prompt measurement the ring renders a dot
 * and says so, rather than inventing a number.
 *
 * Colours mark pressure, not decoration: calm up to 50%, warm past that,
 * hot past 85%.
 */

import { cn } from "@/lib/utils";

const SIZE = 28;
const STROKE = 3;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export type ContextPressure = "calm" | "warm" | "hot";

export interface ContextRingProps {
  /**
   * Percent of the context window used for the last prompt, 0-100. `null`
   * means "no honest measurement" and renders the no-data state.
   */
  percent: number | null;
  /** Tokens the last prompt occupied (display only). */
  usedTokens?: number | null;
  /** The model's context window in tokens (display only). */
  windowTokens?: number | null;
  /** Extra class names for the wrapper. */
  className?: string;
}

/** Pressure band for a percentage. Exported so the tooltip and the stroke
 *  colour can never disagree. */
export function contextPressure(percent: number): ContextPressure {
  if (percent > 85) return "hot";
  if (percent > 50) return "warm";
  return "calm";
}

const STROKE_COLOR: Record<ContextPressure, string> = {
  calm: "#22c55e",
  warm: "#f97316",
  hot: "#ef4444",
};

function fmtTokens(n: number | null | undefined): string {
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
      ? `Context: ${fmtTokens(usedTokens)} tokens used, window unknown`
      : "Context: no measurement yet";
  }
  const shown = Math.min(100, Math.round(percent));
  return (
    `Context window ${shown}% used (${100 - shown}% left) · ` +
    `${fmtTokens(usedTokens)} / ${fmtTokens(windowTokens)} tokens`
  );
}

export function ContextRing({
  percent,
  usedTokens,
  windowTokens,
  className,
}: ContextRingProps) {
  const hasData = percent != null && Number.isFinite(percent);
  const shown = hasData ? Math.min(100, Math.max(0, percent)) : 0;
  const pressure = contextPressure(shown);
  const dashOffset = CIRCUMFERENCE * (1 - shown / 100);
  const label = contextRingLabel(percent ?? null, usedTokens, windowTokens);

  return (
    <span
      className={cn("inline-flex items-center gap-1", className)}
      data-testid="context-ring"
      data-state={hasData ? "measured" : "unmeasured"}
      data-pressure={hasData ? pressure : "none"}
      role="img"
      aria-label={label}
      title={label}
    >
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        aria-hidden="true"
        focusable="false"
      >
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.18}
          strokeWidth={STROKE}
        />
        <circle
          data-testid="context-ring-arc"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke={hasData ? STROKE_COLOR[pressure] : "currentColor"}
          strokeOpacity={hasData ? 1 : 0}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={dashOffset}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          style={{ transition: "stroke-dashoffset 240ms ease" }}
        />
      </svg>
      <span className="sr-only">{label}</span>
      <span aria-hidden="true" className="text-[10px] tabular-nums text-text-tertiary">
        {hasData ? Math.round(shown) : "·"}
      </span>
    </span>
  );
}

/**
 * Turn a receipt's prompt measurement into the ring's percentage.
 *
 * Separated from the component so the arithmetic is testable without a DOM and
 * so the "never use cumulative totals" rule has exactly one home. Returns
 * `null` — not 0 — when either side is missing, because 0 would read as an
 * empty context when the truth is "unknown".
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

export default ContextRing;
