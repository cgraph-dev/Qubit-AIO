/* P3-04 — renderer budget model, sourced from the UI contract.
 *
 * WHAT THIS IS
 * ------------
 * The mandatory budgets in `docs/QUBIT-OFFICE-UI-CONTRACT.md` §3 and
 * `docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md` §6 expressed once, as constants,
 * plus one pure checker. A renderer (or a verifier) hands `checkBudget` the
 * measurements it actually took; the checker never invents a number. A metric
 * that was not measured is reported as `not_measured` with a null value and a
 * null verdict — "unknown" is not "pass".
 *
 * This module holds no canvas, engine or DOM reference. It is the shared
 * vocabulary for the floor renderer, the browser harness and the Python
 * verifier, so the same limit cannot drift between them.
 */

export type DeviceTier = "desktop" | "low";

/** Contract budgets. Keep the source strings — a number without provenance
 *  drifts silently. */
export const BUDGETS = {
  /** P95 frame time at desktop tier. UI contract §3. */
  frameP95DesktopMs: 16.7,
  /** P95 frame time at the low tier. UI contract §3. */
  frameP95LowMs: 33.3,
  /** Fully simulated agents by default. UI contract §3. */
  activeAgents: 40,
  /** Visible avatars; distant avatars are throttled beyond this. UI contract §3. */
  visibleAvatars: 80,
  /** Pathfinding average per frame. UI contract §3. */
  pathfindingAvgMs: 2,
  /** Pathfinding P95 per frame. UI contract §3. */
  pathfindingP95Ms: 8,
  /** Browser heap after 10 min of navigation, low tier. UI contract §3. */
  heapLowMb: 256,
  /** Browser heap after 10 min of navigation, desktop. UI contract §3. */
  heapDesktopMb: 512,
  /** Client events per second, normal. UI contract §3. */
  eventsPerSecondNormal: 20,
  /** Client events per second, burst, with coalescing. UI contract §3. */
  eventsPerSecondBurst: 60,
} as const;

export const BUDGET_SOURCE =
  "docs/QUBIT-OFFICE-UI-CONTRACT.md §3; docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md §6";

/** The raw measurements a caller may supply. Every field is optional on
 *  purpose: absent means "not measured", never zero. */
export interface BudgetSample {
  frameP95Ms?: number | null;
  activeAgents?: number | null;
  visibleAvatars?: number | null;
  pathfindingAvgMs?: number | null;
  pathfindingP95Ms?: number | null;
  heapMb?: number | null;
  eventsPerSecond?: number | null;
  eventBurst?: number | null;
}

export type BudgetMetric =
  | "frame_p95_ms"
  | "active_agents"
  | "visible_avatars"
  | "pathfinding_avg_ms"
  | "pathfinding_p95_ms"
  | "heap_mb"
  | "events_per_second"
  | "event_burst";

export type BudgetStatus = "pass" | "fail" | "not_measured";

export interface BudgetResult {
  metric: BudgetMetric;
  tier: DeviceTier;
  value: number | null;
  limit: number;
  status: BudgetStatus;
  ok: boolean | null;
  source: string;
}

export interface BudgetReport {
  schema: "qubit-office-renderer-budget/1";
  tier: DeviceTier;
  source: string;
  /** `pass` only when every metric was measured and passed; `fail` when any
   *  measured metric exceeded its limit; `not_measured` otherwise. */
  status: BudgetStatus;
  ok: boolean | null;
  measured: number;
  not_measured: number;
  checks: BudgetResult[];
}

interface MetricSpec {
  metric: BudgetMetric;
  field: keyof BudgetSample;
  limit: (tier: DeviceTier) => number;
}

const METRICS: readonly MetricSpec[] = [
  {
    metric: "frame_p95_ms",
    field: "frameP95Ms",
    limit: (tier) =>
      tier === "low" ? BUDGETS.frameP95LowMs : BUDGETS.frameP95DesktopMs,
  },
  { metric: "active_agents", field: "activeAgents", limit: () => BUDGETS.activeAgents },
  { metric: "visible_avatars", field: "visibleAvatars", limit: () => BUDGETS.visibleAvatars },
  { metric: "pathfinding_avg_ms", field: "pathfindingAvgMs", limit: () => BUDGETS.pathfindingAvgMs },
  { metric: "pathfinding_p95_ms", field: "pathfindingP95Ms", limit: () => BUDGETS.pathfindingP95Ms },
  {
    metric: "heap_mb",
    field: "heapMb",
    limit: (tier) => (tier === "low" ? BUDGETS.heapLowMb : BUDGETS.heapDesktopMb),
  },
  {
    metric: "events_per_second",
    field: "eventsPerSecond",
    limit: () => BUDGETS.eventsPerSecondNormal,
  },
  { metric: "event_burst", field: "eventBurst", limit: () => BUDGETS.eventsPerSecondBurst },
];

function isMeasured(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Check a sample against the contract. A missing or non-finite value is
 *  reported `not_measured`; it is never coerced to 0 or to a pass. */
export function checkBudget(
  sample: BudgetSample,
  options: { tier?: DeviceTier } = {},
): BudgetReport {
  const tier: DeviceTier = options.tier === "low" ? "low" : "desktop";
  const checks: BudgetResult[] = METRICS.map((spec) => {
    const raw = sample[spec.field];
    const limit = spec.limit(tier);
    if (!isMeasured(raw)) {
      return {
        metric: spec.metric,
        tier,
        value: null,
        limit,
        status: "not_measured",
        ok: null,
        source: BUDGET_SOURCE,
      };
    }
    const ok = raw <= limit;
    return {
      metric: spec.metric,
      tier,
      value: raw,
      limit,
      status: ok ? "pass" : "fail",
      ok,
      source: BUDGET_SOURCE,
    };
  });
  const measured = checks.filter((row) => row.status !== "not_measured").length;
  const failed = checks.some((row) => row.status === "fail");
  const notMeasured = checks.length - measured;
  const status: BudgetStatus = failed
    ? "fail"
    : notMeasured > 0
      ? "not_measured"
      : "pass";
  return {
    schema: "qubit-office-renderer-budget/1",
    tier,
    source: BUDGET_SOURCE,
    status,
    ok: status === "not_measured" ? null : status === "pass",
    measured,
    not_measured: notMeasured,
    checks,
  };
}

/** Limits for one tier, for a renderer to size its pools before measuring. */
export function tierLimits(tier: DeviceTier): Readonly<{
  frameP95Ms: number;
  activeAgents: number;
  visibleAvatars: number;
  pathfindingAvgMs: number;
  pathfindingP95Ms: number;
  heapMb: number;
  eventsPerSecond: number;
  eventBurst: number;
}> {
  return {
    frameP95Ms:
      tier === "low" ? BUDGETS.frameP95LowMs : BUDGETS.frameP95DesktopMs,
    activeAgents: BUDGETS.activeAgents,
    visibleAvatars: BUDGETS.visibleAvatars,
    pathfindingAvgMs: BUDGETS.pathfindingAvgMs,
    pathfindingP95Ms: BUDGETS.pathfindingP95Ms,
    heapMb: tier === "low" ? BUDGETS.heapLowMb : BUDGETS.heapDesktopMb,
    eventsPerSecond: BUDGETS.eventsPerSecondNormal,
    eventBurst: BUDGETS.eventsPerSecondBurst,
  };
}
