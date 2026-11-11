import { describe, expect, it, vi } from "vitest";
import { createWarRoomPanel } from "./WarRoomPanel";
import {
  allByTestId, byTestId, createHarness, recordingFetch, respond, textOf,
} from "./business-test-harness";

const OVERVIEW = {
  ok: true, schema: "qubit-office-business/1",
  tenants: [{ tenant_id: "ten_1", name: "Acme", created_at: "",
              businesses: [] }],
  default_tenant_id: "ten_1", default_business_id: null,
  counts: { tenants: 1 },
};

function session(over = {}) {
  return {
    session_id: "wrs_1", tenant_id: "ten_1", business_id: null,
    title: "Printable planner", state: "owner_decision",
    brief: { idea: "printable budget planner", niche: "printables" },
    score: { demand: 8, effort: 4, risk: 3 },
    dissent: [{ argument_id: "warg_2", role: "skeptic", stance: "oppose",
                text: "ad costs may dominate" }],
    owner_note: "", created_at: "2026-01-01", updated_at: "2026-01-01",
    ...over,
  };
}

function bundle(over: Record<string, unknown> = {}) {
  return {
    ok: true, schema: "qubit-office-business-warroom-bundle/1",
    tenant_id: "ten_1", session: session(),
    sources: [{ source_id: "wsrc_1", session_id: "wrs_1", kind: "note",
                locator: "owner's own notes", title: "", status: "pending",
                adapter: "", artifact_id: null, text: null, metadata: {},
                created_at: "2026-01-01" }],
    evidence: [{ evidence_id: "wev_1", session_id: "wrs_1",
                 label: "ASSUMPTION", claim: "buyers want a planner",
                 source_id: null, quote: "", metadata: {},
                 created_at: "2026-01-01" }],
    arguments: [
      { argument_id: "warg_1", session_id: "wrs_1", round_no: 1,
        role: "marketer", stance: "support", text: "Pinterest demand is real",
        evidence_ids: [], created_at: "2026-01-01" },
      { argument_id: "warg_2", session_id: "wrs_1", round_no: 1,
        role: "skeptic", stance: "oppose", text: "ad costs may dominate",
        evidence_ids: [], created_at: "2026-01-01" },
    ],
    experiments: [{ experiment_id: "wexp_1", session_id: "wrs_1",
                    name: "Pin A/B", hypothesis: "saves predict sales",
                    method: "two pins", success_metric: "saves",
                    status: "planned", created_at: "2026-01-01" }],
    decisions: [], tasks: [],
    ...over,
  };
}

function backend(overrides: {
  decision?: () => Response | Promise<Response>;
} = {}) {
  let current = bundle();
  let sessions = [current.session];
  const { calls, impl } = recordingFetch((call) => {
    const path = new URL(call.url).pathname;
    if (path.endsWith("/overview")) return respond(200, OVERVIEW);
    if (path.endsWith("/directory")) {
      return respond(200, {
        ok: true, schema: "qubit-office-business-directory/1",
        departments: [{ id: "revenue", title: "Revenue", description: "",
                        service_lines: ["crm"] }],
        roles: Array.from({ length: 132 }, (_, i) => ({
          id: `role-${i}`, department: "revenue", title: `Role ${i}`,
          mission: "", responsibilities: [], skills: [], toolsets: [],
          data_scopes: [], permissions: [], budget: {}, autonomy: "L2",
          approvals: [], schedule: "manual", kpis: [], escalation: "",
          model: { provider_hint: "p", model_hint: "m" }, sop_refs: [],
          memory_refs: [], human_owner: "owner", service_lines: [],
          active_by_default: true,
        })),
        floors: Array.from({ length: 100 }, (_, i) => ({
          floor_id: `floor-${i}`, index: i + 1, wing: i < 50 ? "north" : "south",
          name: `Floor ${i + 1}`, department_ids: ["revenue"],
          role_ids: ["role-0"], room_count: 1, desk_capacity: 4,
          active_by_default: i < 22, max_rooms: null, max_agents: null,
          expansion_allowed: true,
        })),
        counts: { departments: 1, roles: 132, floors: 100,
                  active_floors: 22, dormant_floors: 78, wings: 2 },
      });
    }
    if (path.endsWith("/decisions")) {
      if (overrides.decision) return overrides.decision();
      const decision = { decision_id: "wdec_1", session_id: "wrs_1",
                         status: "approved", decided_by: "owner",
                         note: "ship it", created_at: "2026-01-02" };
      current = bundle({
        session: session({ state: "approved" }),
        decisions: [decision],
      });
      sessions = [current.session];
      return respond(200, { ok: true, created: true, decision });
    }
    if (path.endsWith("/sources")) {
      return respond(200, { ok: true, created: true, source: {
        source_id: "wsrc_2", session_id: "wrs_1", kind: "note",
        locator: "more notes", title: "", status: "pending", adapter: "",
        artifact_id: null, text: null, metadata: {}, created_at: "2026-01-02",
      } });
    }
    if (path.endsWith("/evidence")) {
      return respond(200, { ok: true, created: true, evidence: {
        evidence_id: "wev_2", session_id: "wrs_1", label: "INFERENCE",
        claim: "demand is plausible", source_id: null, quote: "",
        metadata: {}, created_at: "2026-01-02",
      } });
    }
    if (path.endsWith("/tasks")) {
      current = bundle({
        session: session({ state: "tasks_created" }),
        decisions: current.decisions,
        tasks: [{ task_id: "task_1", tenant_id: "ten_1",
                  business_id: "biz_1", title: "draft layouts",
                  status: "open", assigned_to: null,
                  created_at: "2026-01-02", updated_at: "2026-01-02",
                  metadata: {} }],
      });
      return respond(200, { ok: true, tasks: current.tasks });
    }
    if (path.endsWith("/warroom/sessions")) {
      return respond(200, { ok: true, tenant_id: "ten_1", sessions });
    }
    if (path.includes("/warroom/sessions/")) {
      return respond(200, current);
    }
    return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
  });
  return { calls, impl };
}

async function mount(impl: typeof fetch, onClose = vi.fn()) {
  const h = createHarness();
  const Panel = createWarRoomPanel(h.React as never, h.hooks as never);
  h.mount(Panel as never, { base: "", fetchImpl: impl, onClose });
  await h.settle();
  return { h, onClose };
}

describe("War Room renders durable rows", () => {
  it("shows intake sources, evidence labels, debate/dissent, synthesis and decision", async () => {
    const { impl } = backend();
    const { h } = await mount(impl);
    expect(byTestId(h, "warroom")).toHaveLength(1);
    expect(textOf(byTestId(h, "warroom-session-head")[0]))
      .toContain("Printable planner");
    expect(allByTestId(h, "warroom-source")).toHaveLength(1);
    expect(byTestId(h, "warroom-source")[0].props["data-status"]).toBe("pending");
    expect(byTestId(h, "warroom-evidence-item")[0].props["data-label"])
      .toBe("ASSUMPTION");
    expect(allByTestId(h, "warroom-argument")).toHaveLength(2);
    expect(byTestId(h, "warroom-dissent")).toHaveLength(1);
    expect(textOf(byTestId(h, "warroom-brief")[0])).toContain("planner");
    expect(textOf(byTestId(h, "warroom-score")[0])).toContain("demand=8");
    expect(byTestId(h, "warroom-approve")).toHaveLength(1);
    expect(byTestId(h, "warroom-reject")).toHaveLength(1);
  });

  it("approves through the owner door and re-reads the record", async () => {
    const { calls, impl } = backend();
    const { h } = await mount(impl);
    (byTestId(h, "warroom-approve")[0].props.onClick as () => void)();
    await h.settle();
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/decisions"));
    expect(post?.body.owner).toBe(true);
    expect(post?.body.decision).toBe("approved");
    expect(textOf(byTestId(h, "warroom-decision")[0])).toContain("approved");
    expect(textOf(byTestId(h, "warroom-notice")[0])).toContain("recorded");
    expect(byTestId(h, "warroom-create-tasks")).toHaveLength(1);
  });

  it("renders a typed refusal instead of a fake approval", async () => {
    const { impl } = backend({
      decision: () => respond(403, {
        ok: false, code: "BUSINESS_OWNER_ONLY", detail: "owner only",
      }),
    });
    const { h } = await mount(impl);
    (byTestId(h, "warroom-approve")[0].props.onClick as () => void)();
    await h.settle();
    expect(textOf(byTestId(h, "warroom-error")[0]))
      .toContain("BUSINESS_OWNER_ONLY");
    expect(byTestId(h, "warroom-notice")).toHaveLength(0);
  });

  it("shows the role/floor template directory without inventing agents", async () => {
    const { impl } = backend();
    const { h } = await mount(impl);
    (byTestId(h, "warroom-tab-directory")[0].props.onClick as () => void)();
    await h.settle();
    expect(textOf(byTestId(h, "warroom-directory-counts")[0]))
      .toContain("132 roles");
    expect(allByTestId(h, "warroom-role")).toHaveLength(132);
    expect(allByTestId(h, "warroom-floor")).toHaveLength(100);
    expect(allByTestId(h, "warroom-department")).toHaveLength(1);
    // Templates are not agents: no roster/control record is rendered here.
    expect(byTestId(h, "agent-control")).toHaveLength(0);
  });

  it("closes through the host's callback", async () => {
    const onClose = vi.fn();
    const { impl } = backend();
    const { h } = await mount(impl, onClose);
    (byTestId(h, "warroom-close")[0].props.onClick as () => void)();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("empty store is named and the first idea creates the local scope", async () => {
    let created = false;
    const emptyOverview = {
      ok: true, schema: "x", tenants: [], default_tenant_id: null,
      default_business_id: null, counts: { tenants: 0 },
    };
    const { calls, impl } = recordingFetch((call) => {
      const path = new URL(call.url).pathname;
      if (path.endsWith("/overview")) {
        return respond(200, created ? OVERVIEW : emptyOverview);
      }
      if (path.endsWith("/warroom/sessions") && call.method === "POST") {
        created = true;
        return respond(200, { ok: true, created: true, tenant_id: "ten_1",
                              session: session({ state: "intake" }) });
      }
      if (path.endsWith("/warroom/sessions")) {
        return respond(200, { ok: true, tenant_id: "ten_1",
                              sessions: created ? [session({ state: "intake" })] : [] });
      }
      if (path.includes("/warroom/sessions/")) {
        return respond(200, bundle({ session: session({ state: "intake" }) }));
      }
      return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
    });
    const { h } = await mount(impl);
    expect(textOf(byTestId(h, "warroom-empty")[0])).toContain("No Business OS tenant");
    const input = byTestId(h, "warroom-new-title")[0];
    (input.props.onInput as (e: unknown) => void)({ target: { value: "First idea" } });
    h.flush();
    (byTestId(h, "warroom-new-create")[0].props.onClick as () => void)();
    await h.settle();
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/warroom/sessions"));
    expect(post?.body.owner).toBe(true);
    expect(post?.body.title).toBe("First idea");
    expect(textOf(byTestId(h, "warroom-session-head")[0])).toContain("Printable planner");
  });
});
