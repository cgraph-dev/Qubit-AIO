import { describe, expect, it } from "vitest";
import {
  announcement,
  clampIndex,
  cleanQuery,
  COMMAND_CENTER_KINDS,
  COMMAND_CENTER_PATH,
  COMMAND_CENTER_SCHEMA,
  COMMAND_CENTER_SHORTCUTS,
  entryDeepLink,
  entryMatches,
  filterEntries,
  floorDeepLink,
  inboxSummaryText,
  isAttention,
  isEditableTarget,
  kindLabel,
  nextIndex,
  resultSummary,
  rosterSummaryText,
  shortcutAction,
  shortcutHelp,
  statusMarker,
  statusOptions,
  unavailableSources,
  type CommandCenterEntry,
  type CommandCenterProjection,
} from "./command-center";

function entry(overrides: Partial<CommandCenterEntry> = {}): CommandCenterEntry {
  return {
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
      path: "/qubit-office?floor=floor-001&room=floor-001-main&agent=agent-1",
      reason: null,
    },
    ...overrides,
  };
}

const projection = (overrides: Partial<CommandCenterProjection> = {}) =>
  ({
    schema: "qubit-office-command-center/1",
    tenant_id: "ten-1",
    generated_at: "2026-11-14T00:00:00Z",
    state: "ok",
    query: {
      q: "", kind: null, status: null, floor_id: null, room_id: null,
      agent_id: null, task_id: null, approval_id: null, limit: 50,
    },
    counts: {
      floors: 100, rooms: 300, desks: 3075, agents: 2, tasks: 1, sessions: 1,
      approvals: 1, approvals_pending: 1, entries: 4, matched: 2, returned: 2,
    },
    entries: [
      entry(),
      entry({
        entry_id: "approval:apr-1", kind: "approval", title: "publish",
        subtitle: "Ada · pending · risk low", status: "pending",
        source: "approvals", attention: true, agent_id: "agent-1",
        task_id: null, approval_id: "apr-1",
        link: { floor_id: null, room_id: null, agent_id: "agent-1",
          path: null, reason: "no floor is known" },
      }),
    ],
    matched: 2,
    truncated: false,
    roster: {
      total: 2, active: 1, paused: 1, archived: 0, placed: 1, unplaced: 1,
      by_status: { active: 1, paused: 1 }, by_department: { Research: 1 },
    },
    inbox: {
      counts: { approvals_pending: 1, approvals_total: 1 },
      items: [{
        approval_id: "apr-1", action: "publish", requested_by: "agent-1",
        business_id: "biz-1", job_id: null, status: "pending",
        created_at: "2026-11-14T00:00:00Z",
        link: { floor_id: null, room_id: null, agent_id: "agent-1",
          path: null, reason: "no floor is known" },
      }],
    },
    sessions: { total: 1, by_status: { active: 1 } },
    links: { route_template: "/qubit-office?floor={floor_id}&room={room_id}&agent={agent_id}", floors: [] },
    sources: {
      world: { state: "ok", count: 100, code: null, reason: null },
      roster: { state: "ok", count: 2, code: null, reason: null },
      tasks: { state: "ok", count: 1, code: null, reason: null },
      sessions: { state: "ok", count: 1, code: null, reason: null },
      approvals: { state: "ok", count: 1, code: null, reason: null },
    },
    ...overrides,
  }) as CommandCenterProjection;

describe("command-center client", () => {
  it("sends only real filters and drops the all sentinel", () => {
    expect(cleanQuery({ q: "  ada ", kind: "agent", status: "", limit: 20 }))
      .toEqual({ q: "ada", kind: "agent", limit: "20" });
    expect(cleanQuery({ kind: "all", status: "all" })).toEqual({});
  });

  it("names the contract kinds and labels", () => {
    expect(COMMAND_CENTER_PATH).toBe("/command-center");
    expect(COMMAND_CENTER_SCHEMA).toBe("qubit-office-command-center/1");
    expect(COMMAND_CENTER_KINDS).toEqual(
      ["floor", "room", "desk", "agent", "task", "approval"]);
    expect(kindLabel("desk")).toBe("Desk");
    expect(kindLabel("unknown-kind")).toBe("unknown-kind");
  });

  it("matches entries by text and each filter", () => {
    const item = entry();
    expect(entryMatches(item, { q: "ada" })).toBe(true);
    expect(entryMatches(item, { q: "floor-001-main" })).toBe(true);
    expect(entryMatches(item, { q: "nope" })).toBe(false);
    expect(entryMatches(item, { kind: "agent" })).toBe(true);
    expect(entryMatches(item, { kind: "task" })).toBe(false);
    expect(entryMatches(item, { status: "active" })).toBe(true);
    expect(entryMatches(item, { floor_id: "floor-001" })).toBe(true);
    expect(entryMatches(item, { room_id: "floor-001-main" })).toBe(true);
    expect(entryMatches(item, { agent_id: "agent-1" })).toBe(true);
    expect(entryMatches(item, { task_id: "task-1" })).toBe(false);
  });

  it("filters a list and derives real status options", () => {
    const entries = [entry(), entry({ ...entry(), entry_id: "task:1",
      kind: "task", status: "open", task_id: "task-1" })];
    expect(filterEntries(entries, { kind: "task" })).toHaveLength(1);
    expect(filterEntries(entries, { q: "ada" })).toHaveLength(2);
    expect(statusOptions(entries)).toEqual(["active", "open"]);
  });

  it("builds the contract deep link and never guesses a floor", () => {
    expect(floorDeepLink({ floorId: "floor-001", roomId: "room-1",
      agentId: "agent 1" }))
      .toBe("/qubit-office?floor=floor-001&room=room-1&agent=agent+1");
    expect(floorDeepLink({ floorId: "" })).toBe("");
    expect(floorDeepLink({ floorId: "floor-001", base: "/base" }))
      .toBe("/base/qubit-office?floor=floor-001");
    expect(entryDeepLink(entry())).toContain("/qubit-office?floor=floor-001");
    expect(entryDeepLink(entry({ link: { ...entry().link, path: null } })))
      .toContain("room=floor-001-main");
    expect(entryDeepLink(entry({
      floor_id: null,
      link: { floor_id: null, room_id: null, agent_id: null, path: null, reason: "no floor" },
    }))).toBeNull();
  });

  it("maps keyboard events to actions and ignores typing targets", () => {
    expect(shortcutAction({ key: "k", ctrlKey: true })).toBe("focus-search");
    expect(shortcutAction({ key: "k", metaKey: true })).toBe("focus-search");
    expect(shortcutAction({ key: "/" })).toBe("focus-search");
    expect(shortcutAction({ key: "/", target: { tagName: "INPUT" } })).toBeNull();
    expect(shortcutAction({ key: "Escape" })).toBe("close");
    expect(shortcutAction({ key: "ArrowDown" })).toBe("move-next");
    expect(shortcutAction({ key: "ArrowUp" })).toBe("move-prev");
    expect(shortcutAction({ key: "Home" })).toBe("move-first");
    expect(shortcutAction({ key: "End" })).toBe("move-last");
    expect(shortcutAction({ key: "Enter" })).toBe("jump");
    expect(shortcutAction({ key: "a" })).toBeNull();
    expect(isEditableTarget({ tagName: "TEXTAREA" })).toBe(true);
    expect(isEditableTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
    expect(isEditableTarget({ tagName: "DIV" })).toBe(false);
    expect(COMMAND_CENTER_SHORTCUTS.map((s) => s.id)).toContain("close");
    expect(shortcutHelp()).toContain("Escape");
  });

  it("keeps the active index inside a real result list", () => {
    expect(clampIndex(-1, 0)).toBe(-1);
    expect(clampIndex(-5, 3)).toBe(0);
    expect(clampIndex(9, 3)).toBe(2);
    expect(nextIndex(-1, 3, 1)).toBe(0);
    expect(nextIndex(-1, 3, -1)).toBe(2);
    expect(nextIndex(2, 3, 1)).toBe(0);
    expect(nextIndex(0, 3, -1)).toBe(2);
  });

  it("renders status as text markers, never color alone", () => {
    expect(statusMarker("pending")).toBe("!");
    expect(statusMarker("active")).toBe(">");
    expect(statusMarker("done")).toBe("v");
    expect(statusMarker("paused")).toBe("||");
    expect(statusMarker("cancelled")).toBe("x");
    expect(statusMarker("unknown-value")).toBe("?");
    expect(isAttention(entry({ status: "pending", attention: true }))).toBe(true);
    expect(isAttention(entry({ status: "active", attention: false }))).toBe(false);
  });

  it("summarises only the counts the projection carries", () => {
    const p = projection();
    expect(resultSummary(p)).toBe("2 matches.");
    expect(resultSummary(projection({ matched: 0, counts: { ...p.counts, returned: 0 } })))
      .toContain("No floors");
    expect(resultSummary(projection({ truncated: true, matched: 9 })))
      .toContain("showing the first 2");
    expect(rosterSummaryText(p.roster)).toContain("2 agents");
    expect(rosterSummaryText(p.roster)).toContain("1 paused");
    expect(inboxSummaryText(p)).toBe("1 pending approval.");
    expect(inboxSummaryText(projection({ inbox: { counts: { approvals_pending: 0, approvals_total: 0 }, items: [] } })))
      .toBe("No pending approvals.");
  });

  it("turns unavailable sources into a typed warning, not a zero", () => {
    const p = projection({
      sources: {
        world: { state: "ok", count: 100, code: null, reason: null },
        roster: { state: "unavailable", count: 0, code: "DB_BUSY", reason: "busy" },
      },
    });
    expect(unavailableSources(p)).toEqual([
      { name: "roster", code: "DB_BUSY", reason: "busy" },
    ]);
    expect(unavailableSources(projection())).toEqual([]);
  });

  it("announces loading, empty, error and unavailable states", () => {
    expect(announcement({ kind: "loading" })).toContain("Loading");
    expect(announcement({ kind: "error", message: "boom" })).toContain("boom");
    expect(announcement({ kind: "unavailable", code: "NO_TENANT", detail: "none" }))
      .toContain("NO_TENANT");
    expect(announcement({ kind: "available", projection: projection() }))
      .toContain("2 matches");
  });
});
