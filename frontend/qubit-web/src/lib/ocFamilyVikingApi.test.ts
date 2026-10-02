/**
 * Viking family API: ROUTES manifest stays under /api/qubit/viking/* as
 * LOCAL (local engine, always present), the missing-row helper detects
 * not-yet-landed J1 rows, and normalizers handle both envelope shapes
 * without inventing data.
 */
import { describe, expect, it } from "vitest";
import {
  ROUTES,
  VIKING_CONTEXT_GROUPS,
  isBridgeRowMissing,
  normalizeBotSessions,
  normalizeContextTree,
  normalizeSearchHits,
  normalizeVikingFile,
  normalizeVikingSkills,
  normalizeVikingStatus,
  routePath,
  toVikingUri,
} from "@/lib/ocFamilyVikingApi";

describe("ocFamilyVikingApi", () => {
  it("keeps every route under /api/qubit/viking/ as LOCAL", () => {
    expect(ROUTES.length).toBeGreaterThan(0);
    for (const route of ROUTES) {
      expect(route.path.startsWith("/api/qubit/viking/")).toBe(true);
      expect(route.status).toBe("LOCAL");
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

  it("detects missing bridge rows without an unconfigured state", () => {
    expect(isBridgeRowMissing(new Error("GET /api/qubit/viking/status: HTTP 404"))).toBe(true);
    expect(isBridgeRowMissing(new Error("not found"))).toBe(true);
    expect(isBridgeRowMissing({ code: "NOT_FOUND" })).toBe(true);
    expect(isBridgeRowMissing(new Error("boom"))).toBe(false);
    expect(isBridgeRowMissing({ connection: "connected" })).toBe(false);
  });

  it("normalizes status, tree, file, hits, sessions, and skills", () => {
    expect(normalizeVikingStatus({ result: { connection: "connected" } }).connection).toBe("connected");
    expect(normalizeVikingStatus({ result: { connection: "local" } }).connection).toBe("connected");
    expect(normalizeVikingStatus({ nope: 1 }).connection).toBe("unknown");
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
