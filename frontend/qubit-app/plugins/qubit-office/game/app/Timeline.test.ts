import { describe, expect, it, vi } from "vitest";
import { createTimeline } from "./Timeline";
import {
  createHarness,
  recordingFetch,
  respond,
  textOf,
} from "./business-test-harness";
import { item, projection } from "./timeline-fixtures";

function backend(overrides: Record<string, unknown> = {}) {
  return recordingFetch(() => respond(200, {
    ok: true,
    schema: "qubit-office-operator-timeline/1",
    projection: projection(overrides),
  }));
}

function refused() {
  return recordingFetch(() => respond(500, {
    ok: false, code: "BUSINESS_BRIDGE_ERROR", detail: "store unavailable",
  }));
}

describe("operator timeline panel", () => {
  it("announces loading before the read resolves", () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend();
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    const loading = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-loading");
    expect(loading).toBeDefined();
    expect(loading?.props.role).toBe("status");
    h.cleanup();
  });

  it("renders a semantic list with a status marker per item", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({
      items: [
        item(),
        item({
          item_id: "approval:apr-1", kind: "approval", status: "pending",
          pending: true, run_id: null,
        }),
      ],
    });
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();

    const region = h.tree.find(
      (el) => el.props["data-testid"] === "operator-timeline");
    expect(region?.props.role).toBe("region");
    expect(region?.props["aria-label"]).toBe("Operator timeline");

    const list = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-list");
    expect(list?.props.role).toBe("list");
    expect(list?.props["aria-label"]).toBe("Causal operator timeline");

    const rows = h.tree.filter(
      (el) => el.props["data-testid"] === "timeline-item");
    expect(rows).toHaveLength(2);
    expect(rows[0].props.role).toBe("listitem");
    expect(rows[0].props["data-kind"]).toBe("run");
    expect(rows[1].props["data-status"]).toBe("pending");
    // The text marker is present and not decorative-only.
    expect(textOf(rows[0])).toContain("executing");
    expect(textOf(rows[1])).toContain("PENDING");
    h.cleanup();
  });

  it("names an empty timeline instead of rendering blank", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({ state: "empty", items: [] });
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();
    const empty = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-empty");
    expect(empty).toBeDefined();
    expect(empty?.props.role).toBe("status");
    expect(textOf(empty)).toContain("No durable records");
    h.cleanup();
  });

  it("shows a typed error with a retry control", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = refused();
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();
    const error = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-error");
    expect(error).toBeDefined();
    expect(error?.props.role).toBe("alert");
    expect(textOf(error)).toContain("BUSINESS_BRIDGE_ERROR");
    const retry = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-retry");
    expect(typeof retry?.props.onClick).toBe("function");
    h.cleanup();
  });

  it("marks replay explicitly and stubs every item's effect", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({
      mode: "replay",
      replay: {
        enabled: true, effects: "stubbed", real_calls: false,
        label: "Replay (simulated): effects stubbed, no real action performed",
        note: "viewer only",
      },
      items: [item({ replay: true, effect: "stubbed", simulated: true })],
    });
    h.mount(Timeline as never, { fetchImpl: fetch.impl, replay: true });
    await h.settle();
    const marker = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-replay-marker");
    expect(marker).toBeDefined();
    expect(textOf(marker)).toContain("REPLAY");
    const row = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-item");
    expect(row?.props["data-replay"]).toBe("true");
    expect(textOf(row)).toContain("REPLAY");
    const region = h.tree.find(
      (el) => el.props["data-testid"] === "operator-timeline");
    expect(region?.props["data-mode"]).toBe("replay");
    h.cleanup();
  });

  it("never renders a completion claim without evidence as completed", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({
      items: [item({
        status: "completed", claimed_status: "completed",
        completed: false, evidence: false,
        reason: "no artifact or receipt is linked",
      })],
    });
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();
    const row = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-item");
    expect(row?.props["data-status"]).toBe("unknown");
    expect(row?.props["data-completed"]).toBe("false");
    expect(h.tree.find(
      (el) => el.props["data-testid"] === "timeline-item-completed"))
      .toBeUndefined();
    expect(textOf(row)).toContain("UNKNOWN");
    h.cleanup();
  });

  it("renders cost, tokens and evidence links for a real receipt", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({
      items: [
        item(),
        item({
          item_id: "artifact:art-1", kind: "artifact", status: "completed",
          completed: true, evidence: true,
          evidence_refs: [],
          tokens: { prompt: 3, completion: 2, total: 5 },
          cost: { amount_minor: 1200, currency: "USD", direction: "debit" },
        }),
        item({
          item_id: "run:job-2", kind: "run", status: "completed",
          completed: true, evidence: true,
          evidence_refs: [{
            kind: "artifact", id: "art-1", item_id: "artifact:art-1",
          }],
        }),
      ],
    });
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();
    const costs = h.tree.filter(
      (el) => el.props["data-testid"] === "timeline-item-cost");
    expect(costs).toHaveLength(1);
    expect(textOf(costs[0])).toContain("12.00 USD");
    const tokens = h.tree.filter(
      (el) => el.props["data-testid"] === "timeline-item-tokens");
    expect(textOf(tokens[0])).toContain("5 tokens");
    const links = h.tree.filter(
      (el) => el.type === "a" && String(el.props.href).startsWith("#"));
    expect(links.length).toBeGreaterThan(0);
    expect(links[0].props.href).toBe("#tl-artifact-art-1");
    h.cleanup();
  });

  it("supports keyboard navigation over the list items", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({
      items: [item(), item({ item_id: "run:job-2", run_id: "job-2" })],
    });
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();
    const list = () => h.tree.find(
      (el) => el.props["data-testid"] === "timeline-list");
    const rows = () => h.tree.filter(
      (el) => el.props["data-testid"] === "timeline-item");
    expect(rows()[0].props.tabIndex).toBe(0);
    (list()?.props.onKeyDown as (event: { key: string }) => void)({
      key: "ArrowDown",
    });
    h.flush();
    expect(rows()[1].props.tabIndex).toBe(0);
    (list()?.props.onKeyDown as (event: { key: string }) => void)({
      key: "ArrowUp",
    });
    h.flush();
    expect(rows()[0].props.tabIndex).toBe(0);
    h.cleanup();
  });

  it("labels the filters and the close control", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend();
    const onClose = vi.fn();
    h.mount(Timeline as never, { fetchImpl: fetch.impl, onClose });
    await h.settle();
    const search = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-search");
    expect(search?.props["aria-label"]).toBe("Search the operator timeline");
    const kind = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-kind");
    expect(kind?.props["aria-label"]).toBe("Filter by record kind");
    const close = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-close");
    (close?.props.onClick as () => void)();
    expect(onClose).toHaveBeenCalledTimes(1);
    h.cleanup();
  });

  it("renders a partial-source alert without hiding real items", async () => {
    const h = createHarness();
    const Timeline = createTimeline(h.React as never, h.hooks as never);
    const fetch = backend({
      state: "partial",
      sources: {
        ...projection().sources,
        tasks: {
          state: "unavailable", count: 0, code: "OperationalError",
          reason: "busy",
        },
      },
      unavailable_sources: ["tasks"],
    });
    h.mount(Timeline as never, { fetchImpl: fetch.impl });
    await h.settle();
    const partial = h.tree.find(
      (el) => el.props["data-testid"] === "timeline-partial");
    expect(partial).toBeDefined();
    expect(textOf(partial)).toContain("tasks");
    expect(h.tree.filter(
      (el) => el.props["data-testid"] === "timeline-item")).toHaveLength(1);
    h.cleanup();
  });
});
