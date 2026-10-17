/**
 * Slash-catalog merge unit tests (item 1).
 *
 * `mergeSlashCatalog` overlays the live `GET /api/chat/slash-commands` rows
 * on the static registry table; `slashCompletionFor` resolves the canonical
 * completion text for any offered name (alias-aware, so palette picks never
 * send a raw alias); `filterSlashCommands` accepts an explicit catalog.
 */
import { describe, expect, it } from "vitest";

import {
  filterSlashCommands,
  mergeSlashCatalog,
  SLASH_COMMANDS,
  slashCompletionFor,
} from "./slashCommands";

describe("mergeSlashCatalog", () => {
  it("returns the static table when the live read is empty", () => {
    expect(mergeSlashCatalog(null)).toHaveLength(SLASH_COMMANDS.length);
    expect(mergeSlashCatalog([])).toHaveLength(SLASH_COMMANDS.length);
  });

  it("adds /skill-* rows under the Skills category", () => {
    const merged = mergeSlashCatalog([
      { name: "/skill-probe", description: "does things", source: "skill" },
    ]);
    const row = merged.find((c) => c.name === "/skill-probe");
    expect(row).toBeDefined();
    expect(row!.category).toBe("Skills");
    expect(row!.description).toBe("does things");
  });

  it("drops malformed rows instead of fabricating entries", () => {
    const merged = mergeSlashCatalog([
      { name: "no-slash" },
      { name: "/has space" },
      { description: "nameless" },
      null as never,
    ]);
    expect(merged).toHaveLength(SLASH_COMMANDS.length);
  });

  it("live rows win per name over static rows", () => {
    const merged = mergeSlashCatalog([
      { name: "/model", description: "live text", source: "gateway" },
    ]);
    expect(merged.filter((c) => c.name === "/model")).toHaveLength(1);
    expect(
      merged.find((c) => c.name === "/model")!.description,
    ).toBe("live text");
  });
});

describe("slashCompletionFor", () => {
  it("resolves aliases to their canonical command", () => {
    expect(slashCompletionFor("/compact")).toBe("/compress ");
    expect(slashCompletionFor("/tasks")).toBe("/agents ");
    // /model is a bare-run picker command (no trailing space).
    expect(slashCompletionFor("/models")).toBe("/model");
  });

  it("passes unknown names through unchanged", () => {
    expect(slashCompletionFor("/skill-probe")).toBe("/skill-probe ");
  });
});

describe("filterSlashCommands with a merged catalog", () => {
  it("finds live rows by prefix", () => {
    const merged = mergeSlashCatalog([
      { name: "/skill-probe", description: "does things", source: "skill" },
    ]);
    const hits = filterSlashCommands("/skill-pro", 12, merged);
    expect(hits.map((h) => h.name)).toContain("/skill-probe");
  });
});
