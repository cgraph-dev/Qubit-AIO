/**
 * ContextRing — how full the model's context window is, as a small ring.
 *
 * Presentation only. It fetches nothing, owns no timer and never touches the
 * session store: every number arrives as a prop from the ONE page that already
 * holds it (`/qubit-chat` reads `effective_context_length` from
 * `getModelInfo` and `prompt_tokens` from the turn's `done` receipt).
 *
 * The arithmetic and the honesty rule live in `./contextRingMath` — the
 * percentage is the LAST prompt over the window, never cumulative input
 * tokens, and an unknown measurement renders a dot rather than a number.
 * Colours mark pressure, not decoration: calm up to 50%, warm past that, hot
 * past 85%.
 */

import { cn } from "@/lib/utils";

import {
  contextPressure,
  contextRingLabel,
  type ContextPressure,
} from "./contextRingMath";

const SIZE = 28;
const STROKE = 3;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const STROKE_COLOR: Record<ContextPressure, string> = {
  calm: "#22c55e",
  warm: "#f97316",
  hot: "#ef4444",
};

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

export default ContextRing;
