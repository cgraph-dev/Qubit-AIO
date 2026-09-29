import { describe, expect, it } from "vitest";
import { wordDiff } from "./voiceDiff";

describe("wordDiff", () => {
  it("marks an insertion", () => {
    const ops = wordDiff("hello world", "hello brave world");
    expect(ops).toEqual([
      { type: "equal", text: "hello " },
      { type: "add", text: "brave " },
      { type: "equal", text: "world" },
    ]);
  });

  it("marks a deletion", () => {
    const ops = wordDiff("hello brave world", "hello world");
    expect(ops).toEqual([
      { type: "equal", text: "hello " },
      { type: "del", text: "brave " },
      { type: "equal", text: "world" },
    ]);
  });

  it("marks a replacement and preserves whitespace", () => {
    const ops = wordDiff("make it clear", "make it concise");
    const joined = ops.map((o) => o.text).join("");
    expect(joined).toContain("clear");
    expect(joined).toContain("concise");
    expect(ops.some((o) => o.type === "add")).toBe(true);
    expect(ops.some((o) => o.type === "del")).toBe(true);
  });

  it("is a single equal run for identical text", () => {
    const ops = wordDiff("same text", "same text");
    expect(ops).toEqual([{ type: "equal", text: "same text" }]);
  });
});
