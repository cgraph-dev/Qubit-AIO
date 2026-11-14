import { describe, expect, it } from "vitest";
import {
  INVENTORY_SCHEMA,
  PanelInventoryError,
  fetchPanelInventory,
  inventoryFromRows,
  parsePanelInventory,
} from "./panel-inventory";
import { recordingFetch, respond } from "./business-test-harness";

const STATES = [
  "loading", "empty", "error", "retry", "unavailable", "typed_refusal",
];

function row(overrides: Record<string, unknown> = {}) {
  return {
    panel_id: "agents",
    title: "Agents",
    group: "work",
    description: "Roster",
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
    layout: { split: "dock left", stack: "sheet" },
    source_command: "h_business_agents_list",
    ...overrides,
  };
}

function payload(panels: unknown[] = [row()]) {
  return {
    ok: true,
    schema: INVENTORY_SCHEMA,
    required_states: STATES,
    required_panels: ["agents"],
    panels,
  };
}

describe("parsePanelInventory", () => {
  it("accepts a valid backend document", () => {
    const parsed = parsePanelInventory(payload());
    expect(parsed.schema).toBe(INVENTORY_SCHEMA);
    expect(parsed.panels).toHaveLength(1);
    expect(parsed.panels[0].authority.module)
      .toBe("voicebridge.business.agents");
    expect(parsed.panels[0].action.kind).toBe("real");
  });

  it("accepts a typed refusal as a real action alternative", () => {
    const parsed = parsePanelInventory({
      ...payload([
        row({
          panel_id: "deliveries",
          action: {
            kind: "typed_refusal",
            label: "Open delivery file",
            code: "OFFICE_CLIENT_UNSUPPORTED",
            detail: "no deliveries registry",
          },
          permissions: { read: "owner", write: "none" },
        }),
      ]),
      required_panels: ["deliveries"],
    });
    expect(parsed.panels[0].action.kind).toBe("typed_refusal");
  });

  it("rejects a duplicate panel id", () => {
    expect(() => parsePanelInventory(payload([row(), row()])))
      .toThrow(PanelInventoryError);
  });

  it("rejects a real action with no route or module symbol", () => {
    expect(() => parsePanelInventory(payload([
      row({ action: { kind: "real", label: "Do nothing" } }),
    ]))).toThrow(/names neither a route nor a module symbol/);
  });

  it("rejects a renderer declared as the data authority", () => {
    expect(() => parsePanelInventory(payload([
      row({
        authority: {
          module: "frontend.qubit-app.game.app.PanelInventory",
          symbol: "Panel",
        },
      }),
    ]))).toThrow(/renderer/);
  });

  it("rejects an unknown or missing required state", () => {
    expect(() => parsePanelInventory(payload([
      row({ states: [...STATES, "optimistic"] }),
    ]))).toThrow(/unknown states/);
    expect(() => parsePanelInventory(payload([
      row({ states: ["loading", "empty", "error"] }),
    ]))).toThrow(/missing states/);
  });

  it("rejects a missing required panel", () => {
    expect(() => parsePanelInventory({
      ...payload([row()]),
      required_panels: ["agents", "settings"],
    })).toThrow(/missing required panels: settings/);
  });

  it("rejects the wrong schema", () => {
    expect(() => parsePanelInventory({ ...payload(), schema: "wrong" }))
      .toThrow(/schema/);
  });
});

describe("fetchPanelInventory", () => {
  it("fetches the backend catalog route and parses it", async () => {
    const { calls, impl } = recordingFetch(() =>
      respond(200, payload()));
    const read = await fetchPanelInventory({ fetchImpl: impl, base: "" });
    expect(read.kind).toBe("available");
    expect(calls[0].url).toContain(
      "/agent/api/office/business/panel-inventory");
    expect(calls[0].method).toBe("GET");
  });

  it("maps a typed backend refusal to unavailable, not an empty success", async () => {
    const { impl } = recordingFetch(() =>
      respond(403, { ok: false, code: "OWNER_REQUIRED", detail: "owner only" }));
    const read = await fetchPanelInventory({ fetchImpl: impl });
    expect(read.kind).toBe("unavailable");
    if (read.kind === "unavailable") {
      expect(read.code).toBe("OWNER_REQUIRED");
      expect(read.detail).toContain("owner only");
    }
  });

  it("maps a malformed payload to a named error, never a fake catalog", async () => {
    const { impl } = recordingFetch(() =>
      respond(200, { ok: true, schema: INVENTORY_SCHEMA, panels: null }));
    const read = await fetchPanelInventory({ fetchImpl: impl });
    expect(read.kind).toBe("error");
    if (read.kind === "error") {
      expect(read.message).toContain("panels");
    }
  });

  it("maps a transport failure to unavailable", async () => {
    const read = await fetchPanelInventory({
      fetchImpl: (() => { throw new Error("boom"); }) as never,
    });
    expect(read.kind).toBe("unavailable");
  });
});

describe("inventoryFromRows", () => {
  it("validates host-provided rows with the same parser", () => {
    const inventory = inventoryFromRows([row()]);
    expect(inventory.panels[0].panel_id).toBe("agents");
    expect(() => inventoryFromRows([
      row({ authority: { module: "frontend.x", symbol: "Y" } }),
    ])).toThrow(PanelInventoryError);
  });
});
