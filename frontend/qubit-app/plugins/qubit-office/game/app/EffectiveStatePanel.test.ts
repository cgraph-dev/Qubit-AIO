import { describe, expect, it } from "vitest";
import { createEffectiveStatePanel } from "./EffectiveStatePanel";
import {
  allByTestId,
  createHarness,
  recordingFetch,
  respond,
  textOf,
} from "./business-test-harness";

const PROJECTION = {
  schema: "qubit-office-effective-state/1",
  tenant_id: "ten-1",
  agent_id: "agent-1",
  business_id: "biz-1",
  generated_at: "2026-11-14T00:00:00Z",
  agent: { profile: { value: { display_name: "A" }, present: true,
    label: "explicit", source: "bos_agent", reason: null } },
  status: { value: "projection", label: "projection",
    record_status: "active", reason: "seed import" },
  identity: {
    explicit: [{ capability: "read", resource: "feed", action: "read",
      label: "explicit" }],
    inherited: [{ capability: "files", resource: "/tmp", action: "read",
      label: "inherited" }],
    revoked: [{ capability: "payments", resource: "card", action: "charge" }],
    expired: [{ capability: "email", resource: "inbox", action: "send" }],
  },
  capabilities: {
    effective: [
      { capability: "read", resource: "feed", action: "read", label: "explicit" },
      { capability: "files", resource: "/tmp", action: "read", label: "inherited" },
    ],
    denied: [{ capability: "social", resource: "feed", action: "draft" }],
    profile_applied: [], profile_denied: [], revoked: [], reason: null,
  },
  model: {
    primary: { value: { route: "provider-a/model-a" }, present: true,
      label: "explicit", source: "bos_agent", reason: null },
    fallback: { value: { route: "provider-b/model-b" }, present: true,
      label: "explicit", source: "bos_agent", reason: null },
  },
  memory: { scopes: [], reason: "no memory scope recorded" },
  leases: { active: [{}], expired: [{}, {}], released: [], reason: null },
  budget: { currency: "USD", day_spend_minor: 300, month_spend_minor: 300,
    limits: { daily_spend_minor: 1000 } },
  approvals: { pending: [{}], expired: [{}] },
  audit: { events: [{}] },
  links: { tasks: [{}] },
  policy: {},
};

function backend(projection: unknown = PROJECTION) {
  return recordingFetch(() => respond(200, { ok: true, projection }));
}

async function mount(impl: typeof fetch, extra: Record<string, unknown> = {}) {
  const h = createHarness();
  const Panel = createEffectiveStatePanel(h.React as never, h.hooks as never);
  h.mount(Panel as never, { agentId: "agent-1", base: "", fetchImpl: impl,
    ...extra });
  await h.settle();
  return h;
}

describe("EffectiveStatePanel", () => {
  it("renders the projection with explicit/inherited/denied/unknown labels", async () => {
    const { impl } = backend();
    const h = await mount(impl);
    const root = allByTestId(h, "effective-state")[0];
    expect(root.props["data-state"]).toBe("available");
    expect(root.props["aria-label"]).toContain("agent-1");
    expect(textOf(allByTestId(h, "effective-status")[0])).toContain("projection");

    const grants = allByTestId(h, "effective-grant");
    expect(grants.some((g) => g.props["data-state"] === "explicit")).toBe(true);
    expect(grants.some((g) => g.props["data-state"] === "inherited")).toBe(true);
    const denied = allByTestId(h, "effective-denied");
    expect(denied).toHaveLength(1);
    expect(textOf(denied[0])).toContain("social feed:draft");
    expect(textOf(allByTestId(h, "effective-grant-origin")[0]))
      .toContain("1 explicit");
    expect(textOf(allByTestId(h, "effective-model-primary")[0]))
      .toContain("provider-a/model-a");
    expect(allByTestId(h, "effective-model-fallback")[0].props["data-present"])
      .toBe("true");
    expect(allByTestId(h, "effective-lease-counts")[0].props["data-expired"])
      .toBe("2");
    expect(textOf(allByTestId(h, "effective-budget")[0])).toContain("3.00 USD");
    expect(allByTestId(h, "effective-approvals")[0].props["data-pending"])
      .toBe("1");
    expect(textOf(allByTestId(h, "effective-memory")[0]))
      .toContain("no memory scope recorded");
  });

  it("shows a typed error instead of a fake empty allowance", async () => {
    const { impl } = recordingFetch(() =>
      respond(403, { ok: false, code: "OWNER_REQUIRED", detail: "owner only" }));
    const h = await mount(impl);
    const root = allByTestId(h, "effective-state")[0];
    expect(root.props["data-state"]).toBe("unavailable");
    expect(root.props.role).toBe("alert");
    expect(textOf(root)).toContain("OWNER_REQUIRED");
    expect(allByTestId(h, "effective-grant")).toHaveLength(0);
  });

  it("announces loading without claiming a status", async () => {
    const pending = new Promise<Response>(() => {});
    const h = createHarness();
    const Panel = createEffectiveStatePanel(h.React as never, h.hooks as never);
    h.mount(Panel as never, {
      agentId: "agent-1", base: "",
      fetchImpl: (() => pending) as never,
    });
    const root = allByTestId(h, "effective-state")[0];
    expect(root.props["data-state"]).toBe("loading");
    expect(root.props.role).toBe("status");
    expect(textOf(root)).toContain("Loading");
  });

  it("renders an empty capability set as a named reason, not allowed", async () => {
    const empty = {
      ...PROJECTION,
      capabilities: { ...PROJECTION.capabilities, effective: [], denied: [],
        profile_applied: [], profile_denied: [], revoked: [],
        reason: "no grants and no profile scopes" },
      identity: { explicit: [], inherited: [], revoked: [], expired: [] },
    };
    const { impl } = backend(empty);
    const h = await mount(impl);
    expect(textOf(allByTestId(h, "effective-grants-empty")[0]))
      .toContain("no grants and no profile scopes");
    expect(allByTestId(h, "effective-grant")).toHaveLength(0);
  });
});
