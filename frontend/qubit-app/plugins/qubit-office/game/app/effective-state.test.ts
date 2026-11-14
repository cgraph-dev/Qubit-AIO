import { describe, expect, it } from "vitest";
import {
  fetchEffectiveState,
  formatMinor,
  grantLabel,
  leaseCounts,
  modelRoute,
} from "./effective-state";
import { recordingFetch, respond } from "./business-test-harness";

const PROJECTION = {
  schema: "qubit-office-effective-state/1",
  tenant_id: "ten-1",
  agent_id: "agent-1",
  business_id: "biz-1",
  generated_at: "2026-11-14T00:00:00Z",
  agent: { profile: { value: { display_name: "A" }, present: true,
    label: "explicit", source: "bos_agent", reason: null } },
  status: { value: "projection", label: "projection",
    record_status: "active", reason: "seed" },
  identity: { explicit: [], inherited: [], revoked: [], expired: [] },
  capabilities: { effective: [], denied: [], profile_applied: [],
    profile_denied: [], revoked: [], reason: "none" },
  model: { primary: { value: { route: "p/m" }, present: true,
    label: "explicit", source: "bos_agent", reason: null },
  fallback: { value: null, present: false, label: "unknown",
    source: "bos_agent", reason: "no fallback" } },
  memory: { scopes: [], reason: "no scope" },
  leases: { active: [], expired: [], released: [], reason: null },
  budget: { currency: "USD", day_spend_minor: 0, month_spend_minor: 0 },
  approvals: { pending: [], expired: [] },
  audit: { events: [] },
  links: {},
  policy: {},
};

describe("fetchEffectiveState", () => {
  it("returns the backend projection and uses the effective route", async () => {
    const { calls, impl } = recordingFetch(() =>
      respond(200, { ok: true, projection: PROJECTION }));
    const read = await fetchEffectiveState({
      agentId: "agent/1", fetchImpl: impl, base: "",
    });
    expect(read.kind).toBe("available");
    if (read.kind === "available") {
      expect(read.projection.agent_id).toBe("agent-1");
    }
    expect(calls[0].url).toContain("/agents/agent%2F1/effective");
    expect(calls[0].method).toBe("GET");
  });

  it("turns a typed refusal into unavailable, not an empty success", async () => {
    const { impl } = recordingFetch(() =>
      respond(403, { ok: false, code: "OWNER_REQUIRED", detail: "owner only" }));
    const read = await fetchEffectiveState({ agentId: "agent-1", fetchImpl: impl });
    expect(read.kind).toBe("unavailable");
    if (read.kind === "unavailable") {
      expect(read.code).toBe("OWNER_REQUIRED");
      expect(read.detail).toContain("owner only");
    }
  });

  it("keeps a transport failure as a typed unavailable state", async () => {
    const read = await fetchEffectiveState({
      agentId: "agent-1",
      fetchImpl: (() => { throw new Error("boom"); }) as never,
    });
    expect(read.kind).toBe("unavailable");
    if (read.kind === "unavailable") {
      expect(read.code).toBe("BUSINESS_UNREACHABLE");
      expect(read.detail).toContain("could not be reached");
    }
  });

  it("requires an agent id", async () => {
    const read = await fetchEffectiveState({ agentId: "  " });
    expect(read.kind).toBe("unavailable");
    if (read.kind === "unavailable") expect(read.code).toBe("AGENT_ID_REQUIRED");
  });
});

describe("effective-state helpers", () => {
  it("labels grants, model routes, money and leases", () => {
    expect(grantLabel({ capability: "files", resource: "/tmp", action: "read" }))
      .toBe("files /tmp:read");
    expect(modelRoute(PROJECTION.model.primary)).toBe("p/m");
    expect(modelRoute(PROJECTION.model.fallback)).toContain("no fallback");
    expect(formatMinor(300, "USD")).toBe("3.00 USD");
    expect(formatMinor(null, "USD")).toBe("unknown");
    expect(leaseCounts({
      ...PROJECTION,
      leases: { active: [{}], expired: [{}, {}], released: [], reason: null },
    } as never)).toEqual({ active: 1, expired: 2, released: 0 });
  });
});
