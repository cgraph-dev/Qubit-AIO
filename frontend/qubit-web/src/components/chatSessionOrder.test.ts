/**
 * Hermetic tests for the pure session-ordering helper. No DOM, no React — these
 * pin the two guarantees `ChatSessionList` depends on: the active session is
 * always first, and an active id missing from the server list gets a synthetic
 * row instead of vanishing (so the current session can still be highlighted).
 */
import { describe, expect, it } from "vitest";
import { mergeActiveFirst } from "@/components/chatSessionOrder";

interface Row {
  id: string;
  last_active?: number;
  started_at?: number;
  [key: string]: unknown;
}

const rows: Row[] = [
  { id: "a", last_active: 10 },
  { id: "b", last_active: 20 },
  { id: "c", last_active: 30 },
];

describe("mergeActiveFirst", () => {
  it("returns [] for null/undefined/non-array input", () => {
    expect(mergeActiveFirst(null, "a")).toEqual([]);
    expect(mergeActiveFirst(undefined, "a")).toEqual([]);
    expect(
      mergeActiveFirst("nope" as unknown as Row[], "a"),
    ).toEqual([]);
  });

  it("returns [] for an empty array", () => {
    expect(mergeActiveFirst([], "a")).toEqual([]);
  });

  it("moves a present active session to index 0 without mutating input", () => {
    const input = [...rows];
    const out = mergeActiveFirst(input, "c");
    expect(out.map((r) => r.id)).toEqual(["c", "a", "b"]);
    expect(input.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(out).not.toBe(input);
  });

  it("prepends a synthetic row when the active id is absent", () => {
    const out = mergeActiveFirst(rows, "missing", 1234);
    expect(out).toHaveLength(rows.length + 1);
    expect(out[0]).toMatchObject({
      id: "missing",
      name: "missing",
      title: "missing",
      preview: "",
      source: "",
      message_count: 0,
      last_active: 1234,
    });
    expect(out.slice(1).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("leaves order unchanged for a blank or absent activeId", () => {
    expect(mergeActiveFirst(rows, "").map((r) => r.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(mergeActiveFirst(rows, "   ").map((r) => r.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(mergeActiveFirst(rows, null).map((r) => r.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(mergeActiveFirst(rows, undefined).map((r) => r.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("creates no duplicate when the active session is already first", () => {
    const out = mergeActiveFirst(rows, "a");
    expect(out.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(out).toHaveLength(rows.length);
  });
});
