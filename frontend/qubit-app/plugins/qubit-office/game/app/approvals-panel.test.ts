import { describe, expect, it, vi } from "vitest";
import { createApprovalsPanel } from "./ApprovalsPanel";
import {
  ApprovalApiError,
  approvalActionUrl,
  approvalsUrl,
  decideApproval,
  formatAge,
} from "./approvals-panel";
import {
  allByTestId, createHarness, recordingFetch, respond, textOf,
} from "./business-test-harness";

// A fixed clock keeps the age labels deterministic.
const NOW = 1_700_000_100;

const PENDING = [
  {
    id: "apr-1",
    capability: "dashboard.backup",
    summary: "Backup: confirm to proceed.",
    created_at: NOW - 65,
    expires_at: NOW + 300,
    state: "in_progress",
    action: "backup",
    risk: "high",
    requested_by: "dashboard:user",
    session: "dashboard",
  },
  {
    id: "apr-2",
    capability: "toolset.enable",
    summary: "toolset enable 'web' is a medium capability change",
    created_at: NOW - 5,
    expires_at: NOW + 300,
    state: "in_progress",
    action: "enable",
    risk: "medium",
    requested_by: "owner",
    session: "dashboard",
  },
];

function backend(overrides: {
  confirm?: () => Response | Promise<Response>;
  cancel?: () => Response | Promise<Response>;
  list?: () => Response | Promise<Response>;
} = {}) {
  let items = PENDING.map((item) => ({ ...item }));
  const { calls, impl } = recordingFetch((call) => {
    const path = new URL(call.url).pathname;
    if (call.method === "GET" && path.endsWith("/api/ops/approvals")) {
      if (overrides.list) return overrides.list();
      return respond(200, { ok: true, approvals: items, count: items.length });
    }
    if (call.method === "POST" && path.endsWith("/api/ops/confirm")) {
      if (overrides.confirm) return overrides.confirm();
      items = items.filter((item) => item.id !== call.body.confirmation_id);
      return respond(200, { ok: true, state: "executed" });
    }
    if (call.method === "POST" && path.endsWith("/api/ops/cancel")) {
      if (overrides.cancel) return overrides.cancel();
      items = items.filter((item) => item.id !== call.body.confirmation_id);
      return respond(200, { ok: true, state: "executed" });
    }
    return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
  });
  return { calls, impl };
}

async function mount(impl: typeof fetch, extra: Record<string, unknown> = {}) {
  const h = createHarness();
  const Panel = createApprovalsPanel(h.React as never, h.hooks as never);
  const onCountChange = vi.fn();
  const onClose = vi.fn();
  h.mount(Panel as never, {
    base: "",
    fetchImpl: impl,
    onCountChange,
    onClose,
    now: () => NOW,
    ...extra,
  });
  await h.settle();
  return { h, onCountChange, onClose };
}

describe("Office approvals helpers", () => {
  it("builds the compat URLs under /agent and rebases the base path", () => {
    expect(approvalsUrl("")).toBe("/agent/api/ops/approvals");
    expect(approvalsUrl("/dash/")).toBe("/dash/agent/api/ops/approvals");
    expect(approvalActionUrl("approve", "")).toBe("/agent/api/ops/confirm");
    expect(approvalActionUrl("deny", "")).toBe("/agent/api/ops/cancel");
  });

  it("formats ages from epoch seconds without inventing a timestamp", () => {
    expect(formatAge(NOW - 3, NOW)).toBe("3s ago");
    expect(formatAge(NOW - 65, NOW)).toBe("1m ago");
    expect(formatAge(NOW - 7200, NOW)).toBe("2h ago");
    expect(formatAge(NOW - 172800, NOW)).toBe("2d ago");
    expect(formatAge(0, NOW)).toBe("age unknown");
  });

  it("refuses an empty id before it touches the journal", async () => {
    await expect(decideApproval("  ", "approve", {
      fetchImpl: vi.fn() as unknown as typeof fetch,
    })).rejects.toBeInstanceOf(ApprovalApiError);
  });
});

describe("ApprovalsPanel renders the shared journal", () => {
  it("lists every pending record with tool, requester, age and exact id", async () => {
    const { impl } = backend();
    const { h, onCountChange } = await mount(impl);
    const rows = allByTestId(h, "approval-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].props["data-approval-id"]).toBe("apr-1");
    expect(textOf(allByTestId(h, "approval-summary")[0]))
      .toContain("Backup: confirm to proceed.");
    expect(textOf(allByTestId(h, "approval-tool")[0]))
      .toBe("dashboard.backup");
    expect(textOf(allByTestId(h, "approval-requester")[0]))
      .toContain("dashboard:user");
    expect(textOf(allByTestId(h, "approval-age")[0])).toBe("1m ago");
    expect(textOf(allByTestId(h, "approval-id")[0])).toBe("apr-1");
    expect(textOf(allByTestId(h, "approval-action")[0])).toContain("backup");
    expect(textOf(allByTestId(h, "approval-risk")[0])).toContain("high");
    expect(onCountChange).toHaveBeenCalledWith(2);
  });

  it("shows the real pending count in the header and the shell badge", async () => {
    const { impl } = backend();
    const { h } = await mount(impl);
    const count = allByTestId(h, "approvals-count")[0];
    expect(count.props["data-count"]).toBe("2");
    expect(textOf(count)).toBe("2 pending");
    expect(allByTestId(h, "approvals-panel")[0].props["data-count"]).toBe("2");
  });

  it("names a truly empty journal instead of showing a blank list", async () => {
    const { impl } = backend({
      list: () => respond(200, { ok: true, approvals: [], count: 0 }),
    });
    const { h } = await mount(impl);
    expect(allByTestId(h, "approvals-list")).toHaveLength(0);
    expect(textOf(allByTestId(h, "approvals-empty")[0]))
      .toBe("No pending approvals.");
  });

  it("renders a typed read refusal (401) instead of an empty state", async () => {
    const { impl } = backend({ list: () => respond(401, { ok: false }) });
    const { h } = await mount(impl);
    const error = allByTestId(h, "approvals-error")[0];
    expect(error).toBeDefined();
    expect(error.props.role).toBe("alert");
    expect(textOf(error)).toContain("HTTP 401");
  });
});

describe("ApprovalsPanel settles only on an explicit click", () => {
  it("issues no write on mount or refresh", async () => {
    const { calls, impl } = backend();
    const { h } = await mount(impl);
    allByTestId(h, "approvals-refresh")[0].props.onClick();
    await h.settle();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("approves through POST /api/ops/confirm and re-reads the journal", async () => {
    const { calls, impl } = backend();
    const { h } = await mount(impl);
    const approve = allByTestId(h, "approval-approve")[0];
    expect(approve.props["aria-label"]).toContain("dashboard.backup");
    expect(approve.props["aria-label"]).toContain("apr-1");
    expect(approve.props.type).toBe("button");
    (approve.props.onClick as () => void)();
    await h.settle();

    const post = calls.filter((call) => call.method === "POST");
    expect(post).toHaveLength(1);
    expect(new URL(post[0].url).pathname).toBe("/agent/api/ops/confirm");
    expect(post[0].body).toEqual({ confirmation_id: "apr-1" });
    // The list was re-read: only the other record remains.
    expect(allByTestId(h, "approval-row")).toHaveLength(1);
    expect(textOf(allByTestId(h, "approvals-notice")[0]))
      .toContain("Approved dashboard.backup.");
  });

  it("rejects through POST /api/ops/cancel and re-reads the journal", async () => {
    const { calls, impl } = backend();
    const { h } = await mount(impl);
    (allByTestId(h, "approval-reject")[1].props.onClick as () => void)();
    await h.settle();
    const post = calls.filter((call) => call.method === "POST");
    expect(post).toHaveLength(1);
    expect(new URL(post[0].url).pathname).toBe("/agent/api/ops/cancel");
    expect(post[0].body).toEqual({ confirmation_id: "apr-2" });
    expect(allByTestId(h, "approval-row")).toHaveLength(1);
    expect(textOf(allByTestId(h, "approvals-notice")[0]))
      .toContain("Rejected toolset.enable.");
  });

  it("preserves a typed settle refusal and still refreshes", async () => {
    const { calls, impl } = backend({
      confirm: () => respond(400, {
        ok: false, code: "NO_CONFIRMATION",
        detail: "That confirmation is gone.",
      }),
    });
    const { h } = await mount(impl);
    const readsBefore = calls.filter((call) => call.method === "GET").length;
    (allByTestId(h, "approval-approve")[0].props.onClick as () => void)();
    await h.settle();

    const error = allByTestId(h, "approvals-action-error")[0];
    expect(error).toBeDefined();
    expect(error.props.role).toBe("alert");
    expect(textOf(error)).toContain("NO_CONFIRMATION");
    expect(textOf(error)).toContain("That confirmation is gone.");
    // No fabricated success notice, and the journal was re-read.
    expect(allByTestId(h, "approvals-notice")).toHaveLength(0);
    expect(calls.filter((call) => call.method === "GET").length)
      .toBeGreaterThan(readsBefore);
  });
});

describe("ApprovalsPanel is a labelled region", () => {
  it("exposes region/list semantics and a closable, labelled surface", async () => {
    const { impl } = backend();
    const { h, onClose } = await mount(impl);
    const panel = allByTestId(h, "approvals-panel")[0];
    expect(panel.props.role).toBe("region");
    expect(panel.props["aria-label"]).toBe("Office approvals");
    expect(panel.props["aria-busy"]).toBe("false");
    expect(allByTestId(h, "approvals-list")[0].props.role).toBe("list");
    expect(allByTestId(h, "approval-row")[0].props.role).toBe("listitem");
    const close = allByTestId(h, "approvals-close")[0];
    expect(close.props["aria-label"]).toBe("Close approvals");
    (close.props.onClick as () => void)();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
