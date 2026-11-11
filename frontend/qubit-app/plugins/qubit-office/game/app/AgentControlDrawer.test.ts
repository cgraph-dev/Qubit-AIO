import { describe, expect, it, vi } from "vitest";
import { createAgentControlDrawer } from "./AgentControlDrawer";
import { createAgentsPanel } from "./AgentsPanel";
import { matchAgentToSeat, autonomyLabel, budgetSummary } from "./agent-control";
import {
  allByTestId, byTestId, createHarness, recordingFetch, respond, textOf,
} from "./business-test-harness";

function agent(over = {}) {
  return {
    agent_id: "agt_1", tenant_id: "ten_1", business_id: "biz_1",
    role_id: "role.social", department: "Marketing", display_name: "Ada",
    human_owner: "owner", room: "Marketing/2-14", desk: "ada-desk",
    status: "active", provider: "provider-a", model: "model-a",
    reasoning_effort: "high", fallback_model: "model-b",
    fallback_provider: "provider-b", temperature: 0.3, max_tokens: 4096,
    schedule: "0 9 * * 1-5", timezone: "Europe/Bucharest",
    max_concurrent_turns: 2,
    permissions: {
      tools: ["post"], toolsets: ["social"], files: ["/tmp/workspace"],
      browser: [], network: [], terminal: [], email: [], social: ["read", "draft"],
      etsy: [], payments: [], data: [],
    },
    safety: {
      autonomy: "L2", daily_spend_minor: 1000, monthly_spend_minor: 5000,
      per_action_limit_minor: 400, max_turns_per_day: 100,
      max_tokens_per_turn: 8192, rate_limit_per_minute: 30,
      approval_required: ["spend", "legal", "publish", "contact", "irreversible"],
      escalation_target: "human-owner",
    },
    metadata: { office_seat_slug: "ada" }, taken_over_by: null,
    taken_over_at: null, revision: 1, created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z", archived_at: null,
    ...over,
  };
}

const OVERVIEW = {
  ok: true, schema: "qubit-office-business/1",
  tenants: [{ tenant_id: "ten_1", name: "Acme", created_at: "",
              businesses: [{ business_id: "biz_1", name: "Acme", slug: "acme",
                             status: "draft", currency: "USD" }] }],
  default_tenant_id: "ten_1", default_business_id: "biz_1",
  counts: { tenants: 1 },
};

const AUDIT = [{
  event_id: "ev_1", tenant_id: "ten_1", business_id: "biz_1",
  actor: "dashboard:user", action: "agent.create", resource_type: "agent",
  resource_id: "agt_1", outcome: "ok", detail: {}, created_at: "2026-01-01",
}];

function bootstrapBackend(overrides: {
  bootstrap?: () => Response | Promise<Response>;
} = {}) {
  let imported = false;
  const current = agent();
  const { calls, impl } = recordingFetch((call) => {
    const path = new URL(call.url).pathname;
    if (path.endsWith("/bootstrap")) {
      if (overrides.bootstrap) return overrides.bootstrap();
      imported = true;
      return respond(200, {
        ok: true, schema: "qubit-office-business-bootstrap/1",
        tenant_id: "ten_1", business_id: "biz_1",
        created_tenant: true, created_business: true, plan_ok: true,
        total: 1, created: 1, existing: 0, created_slugs: ["ada"],
      });
    }
    if (path.endsWith("/overview")) {
      return respond(200, imported ? OVERVIEW : {
        ok: true, schema: "qubit-office-business/1", tenants: [],
        default_tenant_id: null, default_business_id: null,
        counts: { tenants: 0 },
      });
    }
    if (path.endsWith("/agents/agt_1")) {
      return respond(200, { ok: true, schema: "x", tenant_id: "ten_1",
                            agent: current, audit: AUDIT });
    }
    if (path.endsWith("/agents")) {
      return respond(200, { ok: true, tenant_id: "ten_1",
                            agents: imported ? [current] : [] });
    }
    return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
  });
  return { calls, impl };
}

function backend(overrides: {
  pause?: () => Response | Promise<Response>;
  stop?: () => Response | Promise<Response>;
  revoke?: () => Response | Promise<Response>;
} = {}) {
  let current = agent();
  const { calls, impl } = recordingFetch((call) => {
    const path = new URL(call.url).pathname;
    if (path.endsWith("/overview")) return respond(200, OVERVIEW);
    if (path.endsWith("/pause")) {
      if (overrides.pause) return overrides.pause();
      current = { ...current, status: "paused", revision: 2 };
      return respond(200, { ok: true, agent_id: "agt_1", result: current });
    }
    if (path.endsWith("/resume")) {
      current = { ...current, status: "active", revision: 3 };
      return respond(200, { ok: true, agent_id: "agt_1", result: current });
    }
    if (path.endsWith("/stop")) {
      if (overrides.stop) return overrides.stop();
      return respond(200, { ok: true, agent_id: "agt_1",
        result: { turn: { status: "interrupted" } } });
    }
    if (path.endsWith("/revoke")) {
      if (overrides.revoke) return overrides.revoke();
      current = {
        ...current, status: "revoked",
        permissions: Object.fromEntries(
          Object.keys(current.permissions).map((k) => [k, []])),
      };
      return respond(200, { ok: true, agent_id: "agt_1", result: current });
    }
    if (path.endsWith("/agents/agt_1")) {
      return respond(200, { ok: true, schema: "x", tenant_id: "ten_1",
                            agent: current, audit: AUDIT });
    }
    if (path.endsWith("/agents")) {
      return respond(200, { ok: true, tenant_id: "ten_1", agents: [current] });
    }
    return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
  });
  return { calls, impl, get: () => current };
}

async function mount(impl: typeof fetch, onClose = vi.fn()) {
  const h = createHarness();
  const Drawer = createAgentControlDrawer(
    h.React as never, h.hooks as never);
  h.mount(Drawer as never, {
    seat: { slug: "ada", name: "Ada" }, fetchImpl: impl, onClose,
  });
  await h.settle();
  return { h, onClose };
}

describe("seat -> agent linking is explicit, never guessed", () => {
  it("matches the durable seat key before a display name", () => {
    const linked = agent();
    expect(matchAgentToSeat([linked], {
      slug: "ada", name: "Someone Else",
    } as never)?.agent_id).toBe("agt_1");
    expect(matchAgentToSeat([agent({ metadata: {} })], {
      slug: "nope", name: "Ada",
    } as never)?.agent_id).toBe("agt_1");
    expect(matchAgentToSeat([agent({ metadata: {} })], {
      slug: "nope", name: "Different",
    } as never)).toBeNull();
  });

  it("renders the human autonomy and budget labels", () => {
    expect(autonomyLabel("L2")).toContain("act within budgets");
    expect(budgetSummary(agent() as never)).toContain("$10.00/day");
  });
});

describe("the drawer shows real control data and actions", () => {
  it("renders brain, permissions, limits, autonomy, schedule, status and audit", async () => {
    const { impl } = backend();
    const { h } = await mount(impl);
    expect(byTestId(h, "agent-control")).toHaveLength(1);
    expect(textOf(byTestId(h, "agent-control-provider")[0])).toContain("provider-a");
    expect(textOf(byTestId(h, "agent-control-model")[0])).toContain("model-a");
    expect(textOf(byTestId(h, "agent-control-reasoning")[0])).toContain("high");
    expect(textOf(byTestId(h, "agent-control-budget")[0])).toContain("$10.00/day");
    expect(textOf(byTestId(h, "agent-control-autonomy-level")[0])).toContain("L2");
    expect(textOf(byTestId(h, "agent-control-schedule")[0])).toContain("0 9 * * 1-5");
    expect(textOf(byTestId(h, "agent-control-status")[0])).toContain("active");
    expect(textOf(byTestId(h, "agent-control-permission-summary")[0]))
      .toContain("social: 2");
    expect(allByTestId(h, "agent-control-audit-row")).toHaveLength(1);
  });

  it("pauses through the owner-scoped bridge and re-reads the record", async () => {
    const { calls, impl } = backend();
    const { h } = await mount(impl);
    const pause = byTestId(h, "agent-control-pause")[0];
    (pause.props.onClick as () => void)();
    await h.settle();
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/pause"));
    expect(post?.body.owner).toBe(true);
    expect(textOf(byTestId(h, "agent-control-status")[0])).toContain("paused");
    expect(textOf(byTestId(h, "agent-control-notice")[0])).toContain("recorded");
  });

  it("renders a typed stop refusal instead of a fake success", async () => {
    const { impl } = backend({
      stop: () => respond(409, {
        ok: false, code: "AGENT_NO_ACTIVE_TURN",
        detail: "agent has no active turn to stop",
      }),
    });
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-stop")[0].props.onClick as () => void)();
    await h.settle();
    expect(textOf(byTestId(h, "agent-control-error")[0]))
      .toContain("AGENT_NO_ACTIVE_TURN");
    expect(byTestId(h, "agent-control-notice")).toHaveLength(0);
  });

  it("revokes all scopes only through a POST with owner intent", async () => {
    const { calls, impl } = backend();
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-revoke")[0].props.onClick as () => void)();
    await h.settle();
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/revoke"));
    expect(post?.body.owner).toBe(true);
    expect(textOf(byTestId(h, "agent-control-status")[0])).toContain("revoked");
  });

  it("closes through the host's callback", async () => {
    const onClose = vi.fn();
    const { impl } = backend();
    const { h } = await mount(impl, onClose);
    (byTestId(h, "agent-control-close")[0].props.onClick as () => void)();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("a clicked seat bootstraps its Business OS control record", () => {
  it("imports the roster once when the overview has no tenant", async () => {
    const { calls, impl } = bootstrapBackend();
    const { h } = await mount(impl);
    const posts = calls.filter(
      (c) => c.method === "POST" && c.url.endsWith("/bootstrap"));
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body.owner).toBe(true);
    const overviews = calls.filter(
      (c) => c.method === "GET" && c.url.endsWith("/overview"));
    expect(overviews.length).toBeGreaterThanOrEqual(2);
    // The linked record renders the real control fields, not "no tenant".
    expect(textOf(byTestId(h, "agent-control-provider")[0])).toContain("provider-a");
    expect(textOf(byTestId(h, "agent-control-model")[0])).toContain("model-a");
    expect(byTestId(h, "agent-control-unlinked")).toHaveLength(0);
    expect(byTestId(h, "agent-control-state")).toHaveLength(0);
  });

  it("does not bootstrap when the seat is already linked", async () => {
    const { calls, impl } = backend();
    await mount(impl);
    expect(calls.filter(
      (c) => c.method === "POST" && c.url.endsWith("/bootstrap"))).toHaveLength(0);
  });

  it("shows the typed bootstrap code instead of a fake agent", async () => {
    const { impl } = bootstrapBackend({
      bootstrap: () => respond(409, {
        ok: false, code: "BUSINESS_IDEMPOTENCY_CONFLICT",
        detail: "the bootstrap key is already in use",
      }),
    });
    const { h } = await mount(impl);
    const state = byTestId(h, "agent-control-state")[0];
    expect(state?.props["data-code"]).toBe("BUSINESS_IDEMPOTENCY_CONFLICT");
    expect(textOf(state)).toContain("already in use");
    expect(byTestId(h, "agent-control-provider")).toHaveLength(0);
  });
});

describe("clicking an office seat opens its control drawer", () => {
  it("invokes onOpenAgent with the real seat", async () => {
    const { impl } = recordingFetch(() => respond(200, {
      ok: true, schema: "qubit-office-agents-panel/1",
      counts: { headcount: 1, divisions: 1, roster_divisions: 1,
                boardroom_seats: 0, rooms: 1, floors: 1,
                seat_types: { worker: 1 }, per_division: { alpha: 1 } },
      seats: [{ slug: "ada", name: "Ada", seat_type: "worker",
                supervisor: null, division: "alpha", room: "alpha-desk-a",
                emoji: "🤖", color: "#111", portrait: null, body: null }],
      divisions: [{ slug: "alpha", label: "Alpha", kind: "product", floor: 1,
                    headcount: 1,
                    seats: [{ slug: "ada", name: "Ada", seat_type: "worker",
                              supervisor: null, division: "alpha",
                              room: "alpha-desk-a", emoji: "🤖", color: "#111",
                              portrait: null, body: null }] }],
    }));
    const onOpenAgent = vi.fn();
    const h = createHarness();
    const Panel = createAgentsPanel(h.React as never, h.hooks as never);
    h.mount(Panel as never, { base: "", fetchImpl: impl, onOpenAgent });
    await h.settle();
    const row = allByTestId(h, "agents-panel-seat")
      .find((el) => el.props["data-seat-slug"] === "ada");
    (row?.props.onClick as () => void)();
    expect(onOpenAgent).toHaveBeenCalledTimes(1);
    expect(onOpenAgent.mock.calls[0][0].slug).toBe("ada");
  });
});

function editBackend(overrides: {
  update?: (changes: Record<string, unknown>) => Response | Promise<Response>;
} = {}) {
  let current = agent();
  let audit = [...AUDIT];
  const { calls, impl } = recordingFetch((call) => {
    const path = new URL(call.url).pathname;
    if (path.endsWith("/overview")) return respond(200, OVERVIEW);
    if (path.endsWith("/update")) {
      const changes = (call.body?.changes ?? {}) as Record<string, any>;
      if (overrides.update) return overrides.update(changes);
      const next = { ...current } as Record<string, any>;
      for (const [key, value] of Object.entries(changes)) {
        if (key === "safety") next.safety = { ...current.safety, ...value };
        else if (key === "permissions") next.permissions = value;
        else next[key] = value;
      }
      next.revision = current.revision + 1;
      next.updated_at = "2026-01-02T00:00:00Z";
      current = next as typeof current;
      audit = [{
        event_id: `ev_${audit.length + 1}`, tenant_id: "ten_1",
        business_id: "biz_1", actor: "dashboard:user", action: "agent.update",
        resource_type: "agent", resource_id: "agt_1", outcome: "ok",
        detail: { fields: Object.keys(changes).sort() },
        created_at: "2026-01-02T00:00:00Z",
      }, ...audit];
      return respond(200, { ok: true, tenant_id: "ten_1", agent: current });
    }
    if (path.endsWith("/agents/agt_1")) {
      return respond(200, { ok: true, schema: "x", tenant_id: "ten_1",
                            agent: current, audit });
    }
    if (path.endsWith("/agents")) {
      return respond(200, { ok: true, tenant_id: "ten_1", agents: [current] });
    }
    return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
  });
  return { calls, impl, get: () => current, audit: () => audit };
}

function typeInto(h: ReturnType<typeof createHarness>, id: string,
                  value: string, category?: string) {
  const el = byTestId(h, id).find((node) => category === undefined
    || node.props["data-category"] === category);
  (el?.props.onChange as ((event: unknown) => void)
    | undefined)?.({ target: { value } });
}

describe("agent controls are editable through the real update endpoint", () => {
  it("saves the edited model, budgets, schedule and explicit scopes", async () => {
    const { calls, impl, get, audit } = editBackend();
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-edit")[0].props.onClick as () => void)();
    await h.settle();

    typeInto(h, "agent-control-input-model", "model-z");
    typeInto(h, "agent-control-input-reasoning", "low");
    typeInto(h, "agent-control-input-max-tokens", "2048");
    typeInto(h, "agent-control-input-autonomy", "L3");
    typeInto(h, "agent-control-input-daily-spend", "25.00");
    typeInto(h, "agent-control-input-max-turns-per-day", "50");
    typeInto(h, "agent-control-input-max-tokens-per-turn", "4096");
    typeInto(h, "agent-control-input-rate-limit", "10");
    typeInto(h, "agent-control-input-schedule", "0 8 * * *");
    typeInto(h, "agent-control-input-timezone", "Europe/Paris");
    typeInto(h, "agent-control-perm-input", "reply", "social");
    await h.settle();
    (byTestId(h, "agent-control-perm-add")
      .find((el) => el.props["data-category"] === "social")
      ?.props.onClick as () => void)();
    await h.settle();

    expect(byTestId(h, "agent-control-dirty")).toHaveLength(1);
    (byTestId(h, "agent-control-save")[0].props.onClick as () => void)();
    await h.settle();

    const post = calls.find((c) => c.method === "POST"
      && new URL(c.url).pathname.endsWith("/agents/agt_1/update"));
    expect(post).toBeTruthy();
    expect(post?.body.owner).toBe(true);
    const changes = post?.body.changes;
    expect(changes.model).toBe("model-z");
    expect(changes.reasoning_effort).toBe("low");
    expect(changes.max_tokens).toBe(2048);
    expect(changes.schedule).toBe("0 8 * * *");
    expect(changes.timezone).toBe("Europe/Paris");
    expect(changes.safety).toMatchObject({
      autonomy: "L3", daily_spend_minor: 2500,
      max_turns_per_day: 50, max_tokens_per_turn: 4096,
      rate_limit_per_minute: 10,
    });
    expect(changes.permissions.social).toContain("reply");
    expect(changes.permissions.tools).toEqual(["post"]);
    expect(JSON.stringify(changes.permissions)).not.toContain("*");

    expect(get().model).toBe("model-z");
    expect(textOf(byTestId(h, "agent-control-model")[0])).toContain("model-z");
    expect(textOf(byTestId(h, "agent-control-notice")[0])).toContain("saved");
    // The audit trail was re-read after the save and shows the update.
    const row = allByTestId(h, "agent-control-audit-row")
      .find((el) => el.props["data-action"] === "agent.update");
    expect(row).toBeTruthy();
    expect(audit()[0].action).toBe("agent.update");
    // Saving leaves edit mode, so lifecycle actions are available again.
    expect(byTestId(h, "agent-control-pause")).toHaveLength(1);
    expect(byTestId(h, "agent-control-edit")).toHaveLength(1);
  });

  it("blocks a save with field errors and never calls the endpoint", async () => {
    const { calls, impl } = editBackend();
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-edit")[0].props.onClick as () => void)();
    await h.settle();
    typeInto(h, "agent-control-input-model", "   ");
    await h.settle();
    (byTestId(h, "agent-control-save")[0].props.onClick as () => void)();
    await h.settle();
    expect(calls.some((c) => new URL(c.url).pathname.endsWith("/update")))
      .toBe(false);
    expect(textOf(byTestId(h, "agent-control-field-error-model")[0]))
      .toContain("required");
    expect(textOf(byTestId(h, "agent-control-notice")[0]))
      .toContain("Fix the highlighted fields");
  });

  it("refuses a wildcard scope and adds only the explicit one", async () => {
    const { calls, impl } = editBackend();
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-edit")[0].props.onClick as () => void)();
    await h.settle();

    typeInto(h, "agent-control-perm-input", "*", "tools");
    await h.settle();
    (byTestId(h, "agent-control-perm-add")
      .find((el) => el.props["data-category"] === "tools")
      ?.props.onClick as () => void)();
    await h.settle();
    expect(textOf(byTestId(h, "agent-control-field-error-perm-tools")[0]))
      .toContain("wildcard");
    expect(byTestId(h, "agent-control-perm-chip-tools")).toHaveLength(1);

    typeInto(h, "agent-control-perm-input", "post", "tools");
    await h.settle();
    (byTestId(h, "agent-control-perm-add")
      .find((el) => el.props["data-category"] === "tools")
      ?.props.onClick as () => void)();
    await h.settle();
    expect(byTestId(h, "agent-control-perm-chip-tools")).toHaveLength(1);

    (byTestId(h, "agent-control-save")[0].props.onClick as () => void)();
    await h.settle();
    expect(calls.some((c) => new URL(c.url).pathname.endsWith("/update")))
      .toBe(false);

    typeInto(h, "agent-control-perm-input", "reply", "social");
    await h.settle();
    (byTestId(h, "agent-control-perm-add")
      .find((el) => el.props["data-category"] === "social")
      ?.props.onClick as () => void)();
    await h.settle();
    (byTestId(h, "agent-control-save")[0].props.onClick as () => void)();
    await h.settle();
    const post = calls.find(
      (c) => new URL(c.url).pathname.endsWith("/update"));
    expect(post?.body.changes.permissions.social).toEqual(
      ["draft", "read", "reply"]);
  });

  it("cancels a dirty draft without writing or losing the stored record", async () => {
    const { calls, impl, get } = editBackend();
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-edit")[0].props.onClick as () => void)();
    await h.settle();
    typeInto(h, "agent-control-input-model", "draft-only");
    await h.settle();
    expect(byTestId(h, "agent-control-dirty")).toHaveLength(1);

    (byTestId(h, "agent-control-cancel")[0].props.onClick as () => void)();
    await h.settle();
    expect(calls.some((c) => new URL(c.url).pathname.endsWith("/update")))
      .toBe(false);
    expect(get().model).toBe("model-a");
    expect(textOf(byTestId(h, "agent-control-model")[0])).toContain("model-a");
    expect(byTestId(h, "agent-control-dirty")).toHaveLength(0);
    expect(textOf(byTestId(h, "agent-control-notice")[0]))
      .toContain("cancel");
  });

  it("renders the backend's typed refusal instead of a fake success", async () => {
    const { impl } = editBackend({
      update: () => respond(400, {
        ok: false, code: "AGENT_UNKNOWN_SCOPE",
        detail: "social scope 'root' is a wildcard; name explicit scopes",
      }),
    });
    const { h } = await mount(impl);
    (byTestId(h, "agent-control-edit")[0].props.onClick as () => void)();
    await h.settle();
    typeInto(h, "agent-control-input-model", "model-z");
    await h.settle();
    (byTestId(h, "agent-control-save")[0].props.onClick as () => void)();
    await h.settle();
    const error = byTestId(h, "agent-control-error")[0];
    expect(error?.props["data-code"]).toBe("AGENT_UNKNOWN_SCOPE");
    expect(textOf(error)).toContain("AGENT_UNKNOWN_SCOPE");
    expect(textOf(error)).toContain("wildcard");
    expect(byTestId(h, "agent-control-notice")).toHaveLength(0);
    // The draft stays open so the operator can correct it.
    expect(byTestId(h, "agent-control-save")).toHaveLength(1);
  });
});
