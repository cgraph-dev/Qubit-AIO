import { describe, expect, it } from "vitest";
import {
  causalSort,
  cleanQuery,
  costLabel,
  countsSummary,
  domId,
  effectiveStatus,
  evidenceHref,
  evidenceSummary,
  fetchTimeline,
  filterTimeline,
  formatMinor,
  hasEvidence,
  isCompleted,
  isPending,
  isReplay,
  isReplayItem,
  isUnknown,
  replayLabel,
  statusMarker,
  statusOptions,
  TIMELINE_PATH,
  TIMELINE_SCHEMA,
  tokensLabel,
  unavailableSources,
} from "./timeline";
import { item, projection } from "./timeline-fixtures";
import { recordingFetch, respond } from "./business-test-harness";

describe("timeline route + query", () => {
  it("uses the one operator timeline compat route", () => {
    expect(TIMELINE_PATH).toBe("/operator/timeline");
    expect(TIMELINE_SCHEMA).toBe("qubit-office-operator-timeline/1");
  });

  it("sends only non-empty filters and a bounded replay flag", () => {
    expect(cleanQuery({})).toEqual({});
    expect(cleanQuery({ run: "job-1", limit: 0, replay: true })).toEqual({
      run: "job-1", limit: "1", replay: "1",
    });
    expect(cleanQuery({ run: "  ", task: " task-1 " })).toEqual({
      task: "task-1",
    });
  });
});

describe("causal ordering", () => {
  it("keeps a caused item after its cause even on the same timestamp", () => {
    const cause = item({ item_id: "event:a", kind: "event", at: "t", sequence: 1 });
    const effect = item({
      item_id: "event:b", kind: "event", at: "t", sequence: 2,
      causation_id: "event:a",
    });
    const sorted = causalSort([effect, cause]);
    expect(sorted.map((row) => row.item_id)).toEqual(["event:a", "event:b"]);
  });

  it("falls back to chronological order on a cycle without dropping items", () => {
    const a = item({ item_id: "event:a", at: "1", causation_id: "event:b" });
    const b = item({ item_id: "event:b", at: "2", causation_id: "event:a" });
    const sorted = causalSort([b, a]);
    expect(sorted).toHaveLength(2);
    expect(sorted.map((row) => row.item_id)).toEqual(["event:a", "event:b"]);
  });
});

describe("no fake completion", () => {
  it("a completed status without evidence is reported unknown", () => {
    const fake = item({ status: "completed", completed: false, evidence: false });
    expect(effectiveStatus(fake)).toBe("unknown");
    expect(isCompleted(fake)).toBe(false);
    expect(isUnknown(fake)).toBe(true);
    expect(evidenceSummary(fake)).toContain("unknown");
  });

  it("a completed item with an evidence reference may render complete", () => {
    const real = item({
      status: "completed",
      completed: true,
      evidence: true,
      evidence_refs: [{ kind: "artifact", id: "art-1", item_id: "artifact:art-1" }],
    });
    expect(effectiveStatus(real)).toBe("completed");
    expect(isCompleted(real)).toBe(true);
    expect(hasEvidence(real)).toBe(true);
    expect(evidenceSummary(real)).toContain("artifact art-1");
  });

  it("pending and unknown stay distinct and are never completed", () => {
    const pending = item({ status: "pending", pending: true });
    const unknown = item({ status: "unknown", unknown: true });
    expect(isPending(pending)).toBe(true);
    expect(isCompleted(pending)).toBe(false);
    expect(isUnknown(unknown)).toBe(true);
    expect(isCompleted(unknown)).toBe(false);
  });
});

describe("replay labels", () => {
  it("labels a replay projection and every replay item", () => {
    const replay = projection({
      mode: "replay",
      replay: {
        enabled: true, effects: "stubbed", real_calls: false,
        label: "Replay (simulated): effects stubbed, no real action performed",
        note: "viewer only",
      },
      items: [item({ replay: true, effect: "stubbed", simulated: true })],
    });
    expect(isReplay(replay)).toBe(true);
    expect(replayLabel(replay)).toContain("simulated");
    expect(isReplayItem(replay.items[0])).toBe(true);
    expect(isReplay(projection())).toBe(false);
    expect(replayLabel(projection())).toBeNull();
  });
});

describe("cost, tokens and markers", () => {
  it("formats minor currency and token totals", () => {
    expect(formatMinor(1234, "usd")).toBe("12.34 USD");
    expect(formatMinor(-50, "USD")).toBe("-0.50 USD");
    expect(costLabel({ amount_minor: 1000, currency: "USD" })).toBe("10.00 USD");
    expect(costLabel(null)).toBe("cost unknown");
    expect(tokensLabel({ prompt: 3, completion: 2, total: 5 }))
      .toBe("5 tokens (3 in / 2 out)");
    expect(tokensLabel(null)).toBe("tokens unknown");
  });

  it("uses a text marker so status is never color-only", () => {
    expect(statusMarker("completed")).toBe("[v]");
    expect(statusMarker("unknown")).toBe("[?]");
    expect(statusMarker("pending")).toBe("[..]");
    expect(statusMarker("failed")).toBe("[x]");
    expect(statusMarker("executing")).toBe("[>]");
  });

  it("summarises counts and previews unavailable sources", () => {
    const partial = projection({
      counts: { ...projection().counts, total: 3, completed_with_evidence: 1 },
      sources: {
        ...projection().sources,
        tasks: { state: "unavailable", count: 0, code: "OperationalError",
          reason: "busy" },
      },
      unavailable_sources: ["tasks"],
    });
    expect(countsSummary(partial)).toContain("3 items");
    expect(countsSummary(partial)).toContain("1 completed with evidence");
    expect(unavailableSources(partial)[0]).toMatchObject({
      name: "tasks", code: "OperationalError",
    });
  });

  it("filters locally by query, kind and status", () => {
    const rows = [
      item(),
      item({ item_id: "approval:apr-1", kind: "approval", status: "pending" }),
    ];
    expect(filterTimeline(rows, { kind: "approval" })).toHaveLength(1);
    expect(filterTimeline(rows, { status: "executing" })).toHaveLength(1);
    expect(filterTimeline(rows, { q: "job-1" })).toHaveLength(2);
    expect(statusOptions(rows)).toEqual(["executing", "pending"]);
  });

  it("links evidence only to a record present in the timeline", () => {
    const rows = [item({
      item_id: "artifact:art-1", kind: "artifact", status: "completed",
      evidence: true, completed: true,
    })];
    expect(domId("artifact:art-1")).toBe("tl-artifact-art-1");
    expect(evidenceHref(
      { kind: "artifact", id: "art-1", item_id: "artifact:art-1" }, rows))
      .toBe("#tl-artifact-art-1");
    expect(evidenceHref(
      { kind: "artifact", id: "missing", item_id: "artifact:missing" }, rows))
      .toBeNull();
  });
});

describe("fetchTimeline", () => {
  it("returns the projection on a 200", async () => {
    const { impl } = recordingFetch(() =>
      respond(200, { ok: true, schema: TIMELINE_SCHEMA, projection: projection() }));
    const read = await fetchTimeline({ fetchImpl: impl });
    expect(read.kind).toBe("available");
  });

  it("returns an empty read when the projection has no items", async () => {
    const empty = projection({ state: "empty", items: [] });
    const { impl } = recordingFetch(() =>
      respond(200, { ok: true, schema: TIMELINE_SCHEMA, projection: empty }));
    const read = await fetchTimeline({ fetchImpl: impl });
    expect(read.kind).toBe("empty");
  });

  it("surfaces a typed refusal instead of an empty success", async () => {
    const { impl } = recordingFetch(() => respond(404, {
      ok: false, code: "BUSINESS_NOT_FOUND", detail: "no tenant",
    }));
    const read = await fetchTimeline({ fetchImpl: impl });
    expect(read.kind).toBe("unavailable");
    if (read.kind === "unavailable") {
      expect(read.code).toBe("BUSINESS_NOT_FOUND");
      expect(read.detail).toContain("no tenant");
    }
  });
});
