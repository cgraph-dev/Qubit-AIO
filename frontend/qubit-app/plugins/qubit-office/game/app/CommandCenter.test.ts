import { describe, expect, it, vi } from "vitest";
import { createCommandCenter } from "./CommandCenter";
import {
  allByTestId,
  createHarness,
  recordingFetch,
  respond,
  textOf,
} from "./business-test-harness";

const ENTRY = (overrides: Record<string, unknown> = {}) => ({
  entry_id: "agent:agent-1",
  kind: "agent",
  title: "Ada",
  subtitle: "Research · role.analyst · active",
  status: "active",
  source: "roster",
  attention: false,
  floor_id: "floor-001",
  room_id: "floor-001-main",
  desk_id: "d1",
  agent_id: "agent-1",
  task_id: null,
  approval_id: null,
  link: {
    floor_id: "floor-001",
    room_id: "floor-001-main",
    agent_id: "agent-1",
    path: "/qubit-office/floor/floor-001?room=floor-001-main&agent=agent-1",
    reason: null,
  },
  ...overrides,
});

const PROJECTION = (overrides: Record<string, unknown> = {}) => ({
  schema: "qubit-office-command-center/1",
  tenant_id: "ten-1",
  generated_at: "2026-11-14T00:00:00Z",
  state: "ok",
  query: { q: "", kind: null, status: null, floor_id: null, room_id: null,
    agent_id: null, task_id: null, approval_id: null, limit: 50 },
  counts: { floors: 100, rooms: 300, desks: 3075, agents: 2, tasks: 1,
    sessions: 1, approvals: 1, approvals_pending: 1, entries: 2,
    matched: 2, returned: 2 },
  entries: [
    ENTRY(),
    ENTRY({
      entry_id: "approval:apr-1", kind: "approval", title: "publish",
      subtitle: "Ada · pending · risk low", status: "pending",
      source: "approvals", attention: true, approval_id: "apr-1",
      floor_id: null, room_id: null, desk_id: null,
      link: { floor_id: null, room_id: null, agent_id: "agent-1",
        path: null, reason: "no floor is known" },
    }),
  ],
  matched: 2,
  truncated: false,
  roster: { total: 2, active: 1, paused: 1, archived: 0, placed: 1,
    unplaced: 1, by_status: { active: 1, paused: 1 },
    by_department: { Research: 1 } },
  inbox: { counts: { approvals_pending: 1, approvals_total: 1 },
    items: [{ approval_id: "apr-1", action: "publish",
      requested_by: "agent-1", business_id: "biz-1", job_id: null,
      status: "pending", created_at: "2026-11-14T00:00:00Z",
      link: { floor_id: null, room_id: null, agent_id: "agent-1",
        path: null, reason: "no floor is known" } }] },
  sessions: { total: 1, by_status: { active: 1 } },
  links: { route_template: "/qubit-office/floor/{floor_id}?room={room_id}&agent={agent_id}", floors: [] },
  sources: {
    world: { state: "ok", count: 100, code: null, reason: null },
    roster: { state: "ok", count: 2, code: null, reason: null },
    tasks: { state: "ok", count: 1, code: null, reason: null },
    sessions: { state: "ok", count: 1, code: null, reason: null },
    approvals: { state: "ok", count: 1, code: null, reason: null },
  },
  ...overrides,
});

function backend(projection: unknown = PROJECTION()) {
  return recordingFetch(() => respond(200, { ok: true, projection }));
}

async function mount(impl: typeof fetch, extra: Record<string, unknown> = {}) {
  const h = createHarness();
  const Center = createCommandCenter(h.React as never, h.hooks as never);
  h.mount(Center as never, { base: "", fetchImpl: impl, ...extra });
  await h.settle();
  return h;
}

describe("CommandCenter", () => {
  it("announces loading without claiming a result", async () => {
    const pending = new Promise<Response>(() => {});
    const h = createHarness();
    const Center = createCommandCenter(h.React as never, h.hooks as never);
    h.mount(Center as never, {
      base: "", fetchImpl: (() => pending) as unknown as typeof fetch,
    });
    const state = allByTestId(h, "command-center-state")[0];
    expect(state.props["data-state"]).toBe("loading");
    expect(state.props.role).toBe("status");
    expect(textOf(state)).toContain("Loading");
    h.cleanup();
  });

  it("renders the semantic shell, roster, inbox and results", async () => {
    const { impl } = backend();
    const h = await mount(impl);
    const root = allByTestId(h, "command-center")[0];
    expect(root.props.role).toBe("region");
    expect(root.props["aria-label"]).toBe("Office command center");
    expect(allByTestId(h, "command-center-search")[0].props.role).toBe("search");
    expect(allByTestId(h, "command-center-results")[0].props["aria-label"])
      .toBe("Command center results");
    expect(textOf(allByTestId(h, "command-center-roster")[0])).toContain("2 agents");
    expect(textOf(allByTestId(h, "command-center-roster")[0])).toContain("1 paused");
    expect(allByTestId(h, "command-center-inbox-count")[0].props["data-count"])
      .toBe("1");
    expect(textOf(allByTestId(h, "command-center-inbox")[0])).toContain("publish");
    const entries = allByTestId(h, "command-center-entry");
    expect(entries).toHaveLength(2);
    expect(entries[0].props["data-entry-kind"]).toBe("agent");
    expect(entries[0].props.href)
      .toBe("/qubit-office/floor/floor-001?room=floor-001-main&agent=agent-1");
    expect(entries[1].props["data-attention"]).toBe("true");
    expect(textOf(entries[1])).toContain("needs attention");
    // Status is text, never color alone.
    expect(textOf(entries[1])).toContain("!");
    expect(textOf(entries[1])).toContain("pending");
    h.cleanup();
  });

  it("renders an empty state and no fabricated rows", async () => {
    const empty = PROJECTION({
      state: "empty", entries: [], matched: 0,
      counts: { floors: 0, rooms: 0, desks: 0, agents: 0, tasks: 0,
        sessions: 0, approvals: 0, approvals_pending: 0, entries: 0,
        matched: 0, returned: 0 },
      roster: { total: 0, active: 0, paused: 0, archived: 0, placed: 0,
        unplaced: 0, by_status: {}, by_department: {} },
      inbox: { counts: { approvals_pending: 0, approvals_total: 0 }, items: [] },
      sessions: { total: 0, by_status: {} },
      sources: {
        world: { state: "empty", count: 0, code: null, reason: null },
        roster: { state: "empty", count: 0, code: null, reason: null },
        tasks: { state: "empty", count: 0, code: null, reason: null },
        sessions: { state: "empty", count: 0, code: null, reason: null },
        approvals: { state: "empty", count: 0, code: null, reason: null },
      },
    });
    const { impl } = backend(empty);
    const h = await mount(impl);
    const state = allByTestId(h, "command-center-state")[0];
    expect(state.props["data-state"]).toBe("empty");
    expect(state.props.role).toBe("status");
    expect(textOf(state)).toContain("No office records match");
    expect(allByTestId(h, "command-center-entry")).toHaveLength(0);
    expect(textOf(allByTestId(h, "command-center-roster")[0])).toContain("0 agents");
    expect(textOf(allByTestId(h, "command-center-inbox")[0]))
      .toContain("No pending approvals");
    h.cleanup();
  });

  it("renders a typed unavailable state instead of an empty success", async () => {
    const { impl } = recordingFetch(() =>
      respond(403, { ok: false, code: "OWNER_REQUIRED", detail: "owner only" }));
    const h = await mount(impl);
    const state = allByTestId(h, "command-center-state")[0];
    expect(state.props["data-state"]).toBe("unavailable");
    expect(state.props.role).toBe("alert");
    expect(textOf(state)).toContain("OWNER_REQUIRED");
    expect(allByTestId(h, "command-center-entry")).toHaveLength(0);
    h.cleanup();
  });

  it("names sources that failed without zeroing the rest", async () => {
    const p = PROJECTION({
      sources: {
        world: { state: "unavailable", count: 0, code: "DB_BUSY", reason: "busy" },
        roster: { state: "ok", count: 1, code: null, reason: null },
      },
    });
    const { impl } = backend(p);
    const h = await mount(impl);
    const warning = allByTestId(h, "command-center-sources")[0];
    expect(warning).toBeDefined();
    expect(textOf(warning)).toContain("world (DB_BUSY)");
    expect(allByTestId(h, "command-center-entry").length).toBeGreaterThan(0);
    h.cleanup();
  });

  it("sends search text and filter changes to the backend", async () => {
    const { impl, calls } = backend();
    const h = await mount(impl);
    const query = allByTestId(h, "command-center-query")[0];
    query.props.onChange({ target: { value: "ada" } });
    h.flush();
    await h.settle();
    expect(calls[calls.length - 1].url).toContain("q=ada");
    const kind = allByTestId(h, "command-center-kind")[0];
    kind.props.onChange({ target: { value: "agent" } });
    h.flush();
    await h.settle();
    expect(calls[calls.length - 1].url).toContain("kind=agent");
    const status = allByTestId(h, "command-center-status")[0];
    status.props.onChange({ target: { value: "pending" } });
    h.flush();
    await h.settle();
    expect(calls[calls.length - 1].url).toContain("status=pending");
    h.cleanup();
  });

  it("moves through results with the keyboard and jumps on Enter", async () => {
    const { impl } = backend();
    const onJump = vi.fn();
    const h = await mount(impl, { onJump });
    const root = allByTestId(h, "command-center")[0];
    root.props.onKeyDown({ key: "ArrowDown", preventDefault: () => {} });
    h.flush();
    const entries = allByTestId(h, "command-center-entry");
    expect(entries[1].props["aria-current"]).toBe("true");
    const current = allByTestId(h, "command-center")[0];
    current.props.onKeyDown({ key: "Enter", preventDefault: () => {} });
    expect(onJump).toHaveBeenCalledTimes(1);
    expect(onJump.mock.calls[0][0].entry_id).toBe("approval:apr-1");
    expect(onJump.mock.calls[0][1]).toBeNull();
    h.cleanup();
  });

  it("closes on Escape and focus-search on the shortcut", async () => {
    const { impl } = backend();
    const onClose = vi.fn();
    const h = await mount(impl, { onClose });
    const root = allByTestId(h, "command-center")[0];
    root.props.onKeyDown({ key: "Escape", preventDefault: () => {} });
    expect(onClose).toHaveBeenCalledTimes(1);
    // The search shortcut does not close or select anything.
    root.props.onKeyDown({ key: "k", ctrlKey: true, preventDefault: () => {} });
    expect(onClose).toHaveBeenCalledTimes(1);
    h.cleanup();
  });

  it("keeps the close control labelled", async () => {
    const { impl } = backend();
    const h = await mount(impl);
    const close = allByTestId(h, "command-center-close")[0];
    expect(close.props["aria-label"]).toBe("Close command center");
    expect(textOf(close)).toBe("Close");
    h.cleanup();
  });
});

describe("Command Center tenant setup", () => {
  const BOOTSTRAP = {
    ok: true,
    schema: "qubit-office-business-bootstrap/1",
    tenant_id: "ten_personal",
    business_id: "biz_personal",
    created_tenant: true,
    created_business: true,
    plan_ok: true,
    total: 283,
    created: 283,
    existing: 0,
    created_slugs: ["ceo"],
  };

  function tenantBackend() {
    let reads = 0;
    return recordingFetch((call) => {
      if (call.method === "POST" && call.url.endsWith("/bootstrap")) {
        return respond(200, BOOTSTRAP);
      }
      reads += 1;
      return reads === 1
        ? respond(409, {
          ok: false, code: "BUSINESS_NOT_FOUND",
          detail: "no Business OS tenant exists yet",
        })
        : respond(200, { ok: true, projection: PROJECTION() });
    });
  }

  it("offers an explicit setup instead of an inert failure, and creates nothing until pressed", async () => {
    const { calls, impl } = tenantBackend();
    const h = await mount(impl);

    const setup = allByTestId(h, "business-setup")[0];
    expect(setup).toBeDefined();
    expect(setup.props["data-state"]).toBe("idle");
    expect(setup.props.role).toBe("region");
    expect(textOf(setup)).toContain("not set up yet");
    expect(textOf(setup)).toContain("Nothing is created until you press the button");
    // The panel read is a GET only; opening the panel never bootstraps.
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);

    const start = allByTestId(h, "business-setup-start")[0];
    expect(start.props["aria-label"]).toBe(
      "Create the Qubit Business OS workspace");
    expect(textOf(start)).toBe("Set up Business OS");
    h.cleanup();
  });

  it("bootstraps on press, then retries the read and renders the projection", async () => {
    const { calls, impl } = tenantBackend();
    const h = await mount(impl);
    const start = allByTestId(h, "business-setup-start")[0];
    start.props.onClick();
    await h.settle();

    const posts = calls.filter(
      (call) => call.method === "POST" && call.url.endsWith("/bootstrap"));
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toMatchObject({ owner: true });
    // The retry replaced the setup state with the real projection.
    expect(allByTestId(h, "business-setup")).toHaveLength(0);
    expect(allByTestId(h, "command-center")[0].props["data-state"])
      .toBe("available");
    expect(allByTestId(h, "command-center-entry").length).toBeGreaterThan(0);
    h.cleanup();
  });

  it("names a typed bootstrap failure and keeps the setup available", async () => {
    let reads = 0;
    const { impl } = recordingFetch((call) => {
      if (call.method === "POST" && call.url.endsWith("/bootstrap")) {
        return respond(503, {
          ok: false, code: "BUSINESS_BRIDGE_ERROR",
          detail: "the business store is unavailable",
        });
      }
      reads += 1;
      return respond(409, {
        ok: false, code: "BUSINESS_NOT_FOUND",
        detail: "no Business OS tenant exists yet",
      });
    });
    const h = await mount(impl);
    allByTestId(h, "business-setup-start")[0].props.onClick();
    await h.settle();
    expect(reads).toBeGreaterThanOrEqual(1);

    const error = allByTestId(h, "business-setup-error")[0];
    expect(error).toBeDefined();
    expect(error.props["data-code"]).toBe("BUSINESS_BRIDGE_ERROR");
    expect(error.props.role).toBe("alert");
    expect(textOf(error)).toContain("BUSINESS_BRIDGE_ERROR");
    expect(textOf(error)).toContain("business store is unavailable");
    // Still a setup state with a retry button, never a fake projection.
    expect(allByTestId(h, "business-setup")[0].props["data-state"]).toBe("error");
    expect(allByTestId(h, "business-setup-start")).toHaveLength(1);
    h.cleanup();
  });
});
