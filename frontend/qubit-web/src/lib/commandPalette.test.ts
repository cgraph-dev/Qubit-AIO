/**
 * Command-palette catalog contract for `/qubit-chat`.
 *
 * The palette is the keyboard twin of the page's own voice-commandable
 * actions, so its ranking must be predictable: fuzzy match, actions before
 * commands before reports before sessions, stable ties, and non-matches
 * dropped. Pure functions, so these pin the behaviour without a DOM.
 */
import { describe, expect, it } from "vitest";

import {
  movePaletteIndex,
  rankPaletteItems,
  type PaletteItem,
} from "@/lib/commandPalette";

const items: PaletteItem[] = [
  { id: "act-new", kind: "action", label: "New chat" },
  { id: "act-sum", kind: "action", label: "Summarize this session" },
  { id: "cmd-model", kind: "command", label: "/model", detail: "Switch model" },
  { id: "rep-1", kind: "report", label: "Build report org-api" },
  {
    id: "sess-1",
    kind: "session",
    label: "dashboard-chat",
    detail: "how do I fix the parser",
  },
  { id: "sess-2", kind: "session", label: "voice-7", detail: "weather check" },
];

describe("rankPaletteItems", () => {
  it("returns everything in original order for an empty query", () => {
    expect(rankPaletteItems(items, "").map((i) => i.id)).toEqual(
      items.map((i) => i.id),
    );
  });

  it("filters to matching items only", () => {
    const ids = rankPaletteItems(items, "model").map((i) => i.id);
    expect(ids).toContain("cmd-model");
    expect(ids).not.toContain("sess-2");
  });

  it("matches on detail text, not just the label", () => {
    // "parser" only appears in the session's preview detail.
    expect(rankPaletteItems(items, "parser").map((i) => i.id)).toEqual([
      "sess-1",
    ]);
  });

  it("ranks actions above sessions for an equal-quality match", () => {
    const ranked = rankPaletteItems(
      [
        { id: "sess-x", kind: "session", label: "chat" },
        { id: "act-x", kind: "action", label: "chat" },
      ],
      "chat",
    );
    expect(ranked[0].id).toBe("act-x");
  });

  it("drops disabled placeholder rows", () => {
    const ranked = rankPaletteItems(
      [{ id: "loading", kind: "session", label: "searching…", disabled: true }],
      "",
    );
    expect(ranked.map((i) => i.id)).toEqual(["loading"]);
    expect(rankPaletteItems(
      [{ id: "loading", kind: "session", label: "searching…", disabled: true }],
      "search",
    )).toEqual([]);
  });

  it("keeps tie order stable (original index)", () => {
    const ranked = rankPaletteItems(
      [
        { id: "a", kind: "action", label: "same" },
        { id: "b", kind: "action", label: "same" },
      ],
      "same",
    );
    expect(ranked.map((i) => i.id)).toEqual(["a", "b"]);
  });
});

describe("movePaletteIndex", () => {
  it("wraps forward past the end", () => {
    expect(movePaletteIndex(2, 1, 3)).toBe(0);
  });

  it("wraps backward past the start", () => {
    expect(movePaletteIndex(0, -1, 3)).toBe(2);
  });

  it("returns 0 for an empty list", () => {
    expect(movePaletteIndex(5, 1, 0)).toBe(0);
  });
});
