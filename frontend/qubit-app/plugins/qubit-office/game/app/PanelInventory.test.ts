import { describe, expect, it } from "vitest";
import { createPanelInventoryPanel } from "./PanelInventory";
import {
  allByTestId,
  createHarness,
  recordingFetch,
  respond,
  textOf,
} from "./business-test-harness";

const STATES = [
  "loading", "empty", "error", "retry", "unavailable", "typed_refusal",
];

function row(overrides: Record<string, unknown> = {}) {
  return {
    panel_id: "agents",
    title: "Agents",
    group: "work",
    description: "Seated roster",
    authority: {
      module: "voicebridge.business.agents",
      symbol: "AgentControlService",
      route: "/api/office/business/agents",
      route_method: "GET",
    },
    permissions: { read: "owner", write: "owner" },
    states: [...STATES],
    action: {
      kind: "real",
      label: "Pause agent",
      method: "POST",
      route: "/api/office/business/agents/(?P<id>[^/]+)/pause",
    },
    deep_link: "/qubit-office?panel=agents",
    layout: { split: "dock left", stack: "phone sheet" },
    source_command: "h_business_agents_list",
    ...overrides,
  };
}

function payload(panels: unknown[] = [row()]) {
  return {
    ok: true,
    schema: "qubit-office-panel-inventory/1",
    required_states: STATES,
    required_panels: ["agents"],
    panels,
  };
}

async function mountOnce(extra: Record<string, unknown> = {}) {
  const h = createHarness();
  const Panel = createPanelInventoryPanel(
    h.React as never, h.hooks as never);
  h.mount(Panel as never, { base: "", ...extra });
  await h.settle();
  return h;
}

describe("PanelInventory", () => {
  it("renders every declared panel with authority, permissions, states and a deep link", async () => {
    const h = await mountOnce({ rows: [row()] });
    const root = allByTestId(h, "panel-inventory")[0];
    expect(root.props["data-state"]).toBe("available");
    expect(root.props["aria-label"]).toBe("Panel inventory");
    expect(textOf(allByTestId(h, "panel-inventory-count")[0]))
      .toContain("1 panels declared");

    const panel = allByTestId(h, "panel-row")[0];
    expect(panel.props["data-panel-id"]).toBe("agents");
    expect(panel.props["data-authority"]).toBe(
      "voicebridge.business.agents.AgentControlService");
    expect(panel.props["aria-label"]).toBe("Agents panel");

    expect(textOf(allByTestId(h, "panel-authority")[0]))
      .toContain("voicebridge.business.agents.AgentControlService");
    const permissions = allByTestId(h, "panel-permission");
    expect(permissions.map((p) => p.props["data-permission"]))
      .toEqual(["read", "write"]);
    expect(permissions.map(textOf).join(" "))
      .toContain("Read: owner");
    expect(allByTestId(h, "panel-state-badge")).toHaveLength(6);
    expect(textOf(allByTestId(h, "panel-action")[0]))
      .toContain("POST /api/office/business/agents");
    const link = allByTestId(h, "panel-deep-link")[0];
    expect(link.props.href).toBe("/qubit-office?panel=agents");
    expect(link.props["aria-label"]).toBe("Open Agents");
  });

  it("renders a typed refusal rather than hiding it", async () => {
    const h = await mountOnce({
      rows: [row({
        panel_id: "deliveries",
        title: "Deliveries",
        permissions: { read: "owner", write: "none" },
        action: {
          kind: "typed_refusal",
          label: "Open delivery file",
          code: "OFFICE_CLIENT_UNSUPPORTED",
          detail: "no deliveries registry",
        },
      })],
    });
    const action = allByTestId(h, "panel-action")[0];
    expect(action.props["data-action-kind"]).toBe("typed_refusal");
    expect(action.props["data-action-code"]).toBe("OFFICE_CLIENT_UNSUPPORTED");
    expect(textOf(action)).toContain("refused: OFFICE_CLIENT_UNSUPPORTED");
  });

  it("shows a loading state before data arrives", async () => {
    const never = (() => new Promise(() => {})) as never;
    const h = await mountOnce({ fetchImpl: never });
    const root = allByTestId(h, "panel-inventory")[0];
    expect(root.props["data-state"]).toBe("loading");
    expect(root.props.role).toBe("status");
    expect(textOf(root)).toContain("Loading panel inventory");
  });

  it("shows an honest empty state, not fabricated rows", async () => {
    const h = await mountOnce({ rows: [] });
    const root = allByTestId(h, "panel-inventory")[0];
    expect(root.props["data-state"]).toBe("empty");
    expect(allByTestId(h, "panel-row")).toHaveLength(0);
    expect(textOf(root)).toContain("No panels are declared");
  });

  it("renders a typed error and retries the read", async () => {
    let call = 0;
    const { calls, impl } = recordingFetch(() => {
      call += 1;
      return call === 1
        ? respond(200, { ok: true, schema: "qubit-office-panel-inventory/1", panels: null })
        : respond(200, payload());
    });
    const h = await mountOnce({ fetchImpl: impl });
    const errorRoot = allByTestId(h, "panel-inventory")[0];
    expect(errorRoot.props["data-state"]).toBe("error");
    expect(errorRoot.props.role).toBe("alert");
    const retry = allByTestId(h, "panel-inventory-retry")[0];
    expect(retry.props["aria-label"]).toBe(
      "Retry loading panel inventory");
    retry.props.onClick();
    await h.settle();
    expect(calls).toHaveLength(2);
    expect(allByTestId(h, "panel-inventory")[0].props["data-state"])
      .toBe("available");
    expect(allByTestId(h, "panel-row")).toHaveLength(1);
  });

  it("renders a backend refusal as unavailable with its code", async () => {
    const { impl } = recordingFetch(() =>
      respond(403, { ok: false, code: "OWNER_REQUIRED", detail: "owner only" }));
    const h = await mountOnce({ fetchImpl: impl });
    const root = allByTestId(h, "panel-inventory")[0];
    expect(root.props["data-state"]).toBe("unavailable");
    expect(root.props["data-code"]).toBe("OWNER_REQUIRED");
    expect(textOf(allByTestId(h, "panel-inventory-error-detail")[0]))
      .toContain("owner only");
  });

  it("refuses invalid rows instead of rendering fake state", async () => {
    const h = await mountOnce({
      rows: [row({ states: ["loading", "empty"] })],
    });
    const root = allByTestId(h, "panel-inventory")[0];
    expect(root.props["data-state"]).toBe("error");
    expect(allByTestId(h, "panel-row")).toHaveLength(0);
    expect(textOf(root)).toContain("missing states");
  });

  it("exposes non-color status text and accessible names", async () => {
    const h = await mountOnce({ rows: [row()] });
    const badges = allByTestId(h, "panel-state-badge");
    const labels = badges.map(textOf);
    expect(labels).toContain("Typed refusal");
    expect(labels).toContain("Unavailable");
    expect(labels).toContain("Retryable");
    const list = allByTestId(h, "panel-inventory-list")[0];
    expect(list.props.role).toBe("list");
    expect(list.props["aria-label"]).toBe("Panels");
    expect(textOf(allByTestId(h, "panel-source")[0]))
      .toContain("h_business_agents_list");
  });
});

describe("PanelInventory readable structure", () => {
  it("groups panels into labelled sections instead of one raw text dump", async () => {
    const h = await mountOnce({
      rows: [
        row(),
        row({
          panel_id: "timeline",
          title: "Operator timeline",
          group: "operations",
          description: "Durable run records",
        }),
        row({
          panel_id: "warroom",
          title: "War Room",
          group: "work",
          description: "Idea lab sessions",
        }),
      ],
    });
    const groups = allByTestId(h, "panel-inventory-group");
    expect(groups).toHaveLength(2);
    const groupTitles = groups.map(textOf);
    expect(groupTitles.join(" ")).toContain("Work");
    expect(groupTitles.join(" ")).toContain("Operations");
    // Every declared panel is still a row inside one accessible list.
    expect(allByTestId(h, "panel-row")).toHaveLength(3);
    const list = allByTestId(h, "panel-inventory-list")[0];
    expect(list.props.role).toBe("list");
    expect(list.props["aria-label"]).toBe("Panels");
    expect(String(list.props.className)).toContain("panel-inventory__list");
  });

  it("renders a card header, pills and a muted source line per row", async () => {
    const h = await mountOnce({ rows: [row()] });
    const panel = allByTestId(h, "panel-row")[0];
    expect(String(panel.props.className)).toContain("panel-inventory__row");
    expect(panel.props["data-authority"]).toBe(
      "voicebridge.business.agents.AgentControlService");
    // Headings and labels are real elements, not concatenated text.
    expect(allByTestId(h, "panel-title")[0].type).toBe("h4");
    expect(String(allByTestId(h, "panel-title")[0].props.className))
      .toContain("panel-inventory__title");
    expect(String(allByTestId(h, "panel-authority")[0].props.className))
      .toContain("panel-inventory__authority");
    const badges = allByTestId(h, "panel-state-badge");
    expect(badges.every((badge) =>
      String(badge.props.className).includes("panel-inventory__pill"))).toBe(true);
    const source = allByTestId(h, "panel-source")[0];
    expect(String(source.props.className)).toContain("panel-inventory__muted");
    const link = allByTestId(h, "panel-deep-link")[0];
    expect(String(link.props.className)).toContain("panel-inventory__link");
  });
});
