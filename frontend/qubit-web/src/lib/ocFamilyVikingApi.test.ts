/**
 * Viking family API: ROUTES manifest stays under /api/qubit/viking/* as
 * NEEDS-BRIDGE, the SERVER_UNCONFIGURED gate is typed, and normalizers
 * handle both envelope shapes without inventing data.
 */
import { describe, expect, it } from "vitest";
import {
  ROUTES,
  VIKING_CONTEXT_GROUPS,
  VikingServerUnconfiguredError,
  isServerUnconfiguredPayload,
  normalizeBotSessions,
  normalizeContextTree,
  normalizeSearchHits,
  normalizeVikingFile,
  normalizeVikingSkills,
  normalizeVikingStatus,
  routePath,
  throwIfServerUnconfigured,
  toVikingUri,
} from "@/lib/ocFamilyVikingApi";

describe("ocFamilyVikingApi", () => {
  it("keeps every route under /api/qubit/viking/ as NEEDS-BRIDGE", () => {
    expect(ROUTES.length).toBeGreaterThan(0);
    for (const route of ROUTES) {
      expect(route.path.startsWith("/api/qubit/viking/")).toBe(true);
      expect(route.status).toBe("NEEDS-BRIDGE");
    }
    expect(routePath("status")?.path).toBe("/api/qubit/viking/status");
    expect(routePath("resource_add")?.method).toBe("POST");
  });

  it("covers the nine console tree groups", () => {
    expect(VIKING_CONTEXT_GROUPS.map((g) => g.key)).toEqual([
      "user_memories",
      "bot",
      "peers",
      "entities",
      "preferences",
      "profiles",
      "sessions",
      "skills",
      "resources",
    ]);
  });

  it("types the SERVER_UNCONFIGURED gate", () => {
    expect(isServerUnconfiguredPayload({ code: "SERVER_UNCONFIGURED" })).toBe(true);
    expect(isServerUnconfiguredPayload({ result: { code: "SERVER_UNCONFIGURED" } })).toBe(true);
    expect(isServerUnconfiguredPayload({ connection: "connected" })).toBe(false);
    expect(() => throwIfServerUnconfigured({ code: "SERVER_UNCONFIGURED" })).toThrow(
      VikingServerUnconfiguredError,
    );
    expect(() => throwIfServerUnconfigured({ connection: "connected" })).not.toThrow();
  });

  it("normalizes status, tree, file, hits, sessions, and skills", () => {
    expect(normalizeVikingStatus({ result: { connection: "connected" } }).connection).toBe("connected");
    const tree = normalizeContextTree({
      tree: { user_memories: [{ id: "m1", label: "note", path: "memories/m1.md" }] },
    });
    expect(tree.user_memories?.[0]?.label).toBe("note");
    const file = normalizeVikingFile({ result: { path: "a/b.md", content: "hi" } });
    expect(file?.vikingUri).toBe("viking://a/b.md");
    expect(normalizeSearchHits({ hits: [{ id: "h", title: "t" }] })).toHaveLength(1);
    expect(normalizeBotSessions({ sessions: [{ id: "s" }] })).toHaveLength(1);
    expect(normalizeVikingSkills({ skills: [{ id: "k" }] })).toHaveLength(1);
    expect(toVikingUri("/x/y.md")).toBe("viking://x/y.md");
  });
});
