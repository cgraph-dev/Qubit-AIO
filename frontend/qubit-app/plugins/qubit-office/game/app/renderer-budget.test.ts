import { describe, expect, it } from "vitest";
import {
  BUDGETS,
  BUDGET_SOURCE,
  checkBudget,
  tierLimits,
  type BudgetSample,
} from "./renderer-budget";

const ALL_PASSING: BudgetSample = {
  frameP95Ms: 16,
  activeAgents: 40,
  visibleAvatars: 80,
  pathfindingAvgMs: 2,
  pathfindingP95Ms: 8,
  heapMb: 512,
  eventsPerSecond: 20,
  eventBurst: 60,
};

describe("checkBudget", () => {
  it("passes every measured metric inside the desktop limits", () => {
    const report = checkBudget(ALL_PASSING);
    expect(report.status).toBe("pass");
    expect(report.ok).toBe(true);
    expect(report.measured).toBe(8);
    expect(report.not_measured).toBe(0);
    expect(report.tier).toBe("desktop");
    expect(report.source).toBe(BUDGET_SOURCE);
    expect(report.checks.every((check) => check.status === "pass")).toBe(true);
  });

  it("reports each exceeded metric as a typed fail", () => {
    const report = checkBudget({
      ...ALL_PASSING,
      frameP95Ms: 20,
      activeAgents: 41,
      visibleAvatars: 81,
      pathfindingAvgMs: 3,
      pathfindingP95Ms: 9,
      heapMb: 600,
      eventsPerSecond: 21,
      eventBurst: 61,
    });
    expect(report.status).toBe("fail");
    expect(report.ok).toBe(false);
    const fails = report.checks.filter((check) => check.status === "fail");
    expect(fails.map((check) => check.metric).sort()).toEqual([
      "active_agents",
      "event_burst",
      "events_per_second",
      "frame_p95_ms",
      "heap_mb",
      "pathfinding_avg_ms",
      "pathfinding_p95_ms",
      "visible_avatars",
    ]);
  });

  it("never fabricates a value for an unmeasured metric", () => {
    const report = checkBudget({});
    expect(report.status).toBe("not_measured");
    expect(report.ok).toBeNull();
    expect(report.measured).toBe(0);
    expect(report.not_measured).toBe(8);
    for (const check of report.checks) {
      expect(check.status).toBe("not_measured");
      expect(check.value).toBeNull();
      expect(check.ok).toBeNull();
    }
    // The limit is declared; the measurement is not.
    expect(report.checks.find((c) => c.metric === "frame_p95_ms")?.limit)
      .toBe(BUDGETS.frameP95DesktopMs);
  });

  it("treats null and non-finite inputs as not measured, not as zero", () => {
    const report = checkBudget({
      frameP95Ms: null,
      heapMb: Number.NaN,
      pathfindingAvgMs: Infinity,
    });
    for (const check of report.checks) {
      expect(check.status).toBe("not_measured");
      expect(check.value).toBeNull();
    }
  });

  it("uses the low-tier frame and heap limits when asked", () => {
    const low = checkBudget(
      { ...ALL_PASSING, frameP95Ms: 30, heapMb: 200 },
      { tier: "low" },
    );
    expect(low.tier).toBe("low");
    expect(low.status).toBe("pass");
    const desktop = checkBudget(
      { ...ALL_PASSING, frameP95Ms: 30, heapMb: 200 },
      { tier: "desktop" },
    );
    expect(desktop.status).toBe("fail");
    expect(desktop.checks.find((c) => c.metric === "frame_p95_ms")?.limit)
      .toBe(16.7);
    expect(desktop.checks.find((c) => c.metric === "heap_mb")?.limit).toBe(512);
  });

  it("fails when one metric is measured and over budget even if others are absent", () => {
    const report = checkBudget({ activeAgents: 41 });
    expect(report.status).toBe("fail");
    expect(report.ok).toBe(false);
    expect(report.measured).toBe(1);
    expect(report.not_measured).toBe(7);
  });

  it("is deterministic for repeated checks", () => {
    expect(checkBudget(ALL_PASSING)).toEqual(checkBudget(ALL_PASSING));
  });
});

describe("tierLimits", () => {
  it("mirrors the contract per tier", () => {
    expect(tierLimits("desktop")).toMatchObject({
      frameP95Ms: 16.7,
      heapMb: 512,
      eventsPerSecond: 20,
      eventBurst: 60,
    });
    expect(tierLimits("low")).toMatchObject({
      frameP95Ms: 33.3,
      heapMb: 256,
    });
  });
});
