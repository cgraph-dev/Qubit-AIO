/**
 * Contract tests for the C1 activity reducers.
 *
 * These are the pure halves of `useChatActivity` — the SSE wiring is thin on
 * top of them. Pinning them here means a wire-shape change (the vendored todo
 * normalizer is producer-owned) shows up as a test failure instead of an empty
 * panel in the UI.
 */
import { describe, expect, it } from "vitest";
import {
  normalizeTodoState,
  reduceSubagentFrame,
  resolveActivityFrame,
  type AgentActivityEntry,
} from "@/hooks/useChatActivity";

describe("normalizeTodoState", () => {
  it("reads the {todos, revision} envelope", () => {
    const rows = normalizeTodoState({
      revision: 3,
      todos: [
        { id: "a", text: "write the plan", status: "completed" },
        { id: "b", text: "build it", status: "in_progress" },
      ],
    });
    expect(rows).toEqual([
      { id: "a", text: "write the plan", done: true },
      { id: "b", text: "build it", done: false },
    ]);
  });

  it("accepts the common alternative field spellings", () => {
    const rows = normalizeTodoState({
      todos: [{ content: "alt text", done: true }],
    });
    expect(rows[0]?.text).toBe("alt text");
    expect(rows[0]?.done).toBe(true);
  });

  it("treats a missing status as not done", () => {
    const rows = normalizeTodoState({ todos: [{ text: "x" }] });
    expect(rows[0]?.done).toBe(false);
  });

  it("returns [] for a malformed payload rather than throwing", () => {
    expect(normalizeTodoState(null)).toEqual([]);
    expect(normalizeTodoState({})).toEqual([]);
    expect(normalizeTodoState({ todos: "nope" })).toEqual([]);
    expect(normalizeTodoState({ todos: [null, 42] })).toEqual([]);
  });

  it("skips rows with no text", () => {
    expect(normalizeTodoState({ todos: [{ id: "a" }] })).toEqual([]);
  });

  it("synthesizes an id when the row has none", () => {
    const rows = normalizeTodoState({ todos: [{ text: "no id" }] });
    expect(rows[0]?.id).toBeTruthy();
  });
});

describe("reduceSubagentFrame", () => {
  const start = (id: string, goal: string) => [
    { id, goal, status: "running" },
  ];

  it("adds a child on subagent.start", () => {
    const next = reduceSubagentFrame([], "subagent.start", {
      subagent_id: "s1",
      goal: "research the thing",
    });
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ id: "s1", goal: "research the thing" });
  });

  it("updates in place rather than duplicating", () => {
    const first = reduceSubagentFrame([], "subagent.start", {
      subagent_id: "s1",
      goal: "g",
    });
    const second = reduceSubagentFrame(first, "subagent.tool", {
      subagent_id: "s1",
      tool_name: "terminal",
      tool_count: 4,
    });
    expect(second).toHaveLength(1);
    expect(second[0]).toMatchObject({ id: "s1", lastTool: "terminal", toolCount: 4 });
  });

  it("keeps an earlier goal when a later progress frame omits it", () => {
    const first = start("s1", "keep me");
    const second = reduceSubagentFrame(first, "subagent.progress", {
      subagent_id: "s1",
      tool_name: "read_file",
    });
    expect(second[0]?.goal).toBe("keep me");
  });

  it("removes the child on subagent.complete", () => {
    const first = start("s1", "g");
    expect(reduceSubagentFrame(first, "subagent.complete", { subagent_id: "s1" })).toEqual([]);
  });

  it("leaves the roster alone for a frame with no identity", () => {
    const first: AgentActivityEntry[] = [{ id: "s1", goal: "g", status: "running" }];
    expect(reduceSubagentFrame(first, "subagent.start", {})).toBe(first);
    expect(reduceSubagentFrame(first, "subagent.start", null)).toBe(first);
  });

  it("tracks several children independently", () => {
    let roster = reduceSubagentFrame([], "subagent.start", { subagent_id: "a", goal: "A" });
    roster = reduceSubagentFrame(roster, "subagent.start", { subagent_id: "b", goal: "B" });
    roster = reduceSubagentFrame(roster, "subagent.complete", { subagent_id: "a" });
    expect(roster.map((r) => r.id)).toEqual(["b"]);
  });
});

describe("resolveActivityFrame (CUSTOM envelope unwrap)", () => {
  // Captured verbatim from the live feed ``GET /api/events/stream`` (one
  // ``data:`` body). Pinned as a literal so a wire-shape regression fails here
  // instead of silently blanking the panel.
  const TODO_CUSTOM =
    '{"type":"CUSTOM","name":"todo.updated","value":{"todos":[{"id":"t1","text":"ship the panel","status":"in_progress"},{"id":"t2","text":"write the test","status":"completed"}],"revision":7}}';

  it("reads the real name from rec.name, not the literal type CUSTOM", () => {
    const rec = JSON.parse(TODO_CUSTOM) as Record<string, unknown>;
    expect(rec.type).toBe("CUSTOM");
    const { name, payload } = resolveActivityFrame(rec);
    expect(name).toBe("todo.updated");
    expect(payload).toEqual({
      todos: [
        { id: "t1", text: "ship the panel", status: "in_progress" },
        { id: "t2", text: "write the test", status: "completed" },
      ],
      revision: 7,
    });
  });

  it("dispatches a captured CUSTOM todo.updated frame into non-empty todos", () => {
    const rec = JSON.parse(TODO_CUSTOM) as Record<string, unknown>;
    const { name, payload } = resolveActivityFrame(rec);
    // Mirror the hook's dispatch: name === "todo.updated" -> normalize value.
    expect(name).toBe("todo.updated");
    const todos = normalizeTodoState(payload);
    expect(todos).toEqual([
      { id: "t1", text: "ship the panel", done: false },
      { id: "t2", text: "write the test", done: true },
    ]);
  });

  it("dispatches a captured CUSTOM subagent.start frame into the roster", () => {
    const rec = JSON.parse(
      '{"type":"CUSTOM","name":"subagent.start","value":{"subagent_id":"s1","goal":"research"}}',
    ) as Record<string, unknown>;
    const { name, payload } = resolveActivityFrame(rec);
    expect(name.startsWith("subagent.")).toBe(true);
    expect(reduceSubagentFrame([], name, payload)).toEqual([
      { id: "s1", goal: "research", status: "running", lastTool: undefined, toolCount: undefined, depth: undefined },
    ]);
  });

  it("keeps the legacy non-CUSTOM name/payload resolution byte-identical", () => {
    const rec = { type: "STATE_SNAPSHOT", snapshot: { a: 1 } };
    const { name, payload } = resolveActivityFrame(rec);
    expect(name).toBe("STATE_SNAPSHOT");
    // Non-CUSTOM falls back to rec.data ?? rec.payload ?? rec.
    expect(payload).toBe(rec);
  });

  it("falls back to rec.event when a CUSTOM frame omits name", () => {
    const rec = { type: "CUSTOM", event: "subagent.complete", value: { subagent_id: "s1" } };
    expect(resolveActivityFrame(rec).name).toBe("subagent.complete");
  });
});
