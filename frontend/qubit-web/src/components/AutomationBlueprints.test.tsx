// @vitest-environment jsdom
/**
 * /cron Blueprints crash regression.
 *
 * The Qubit compat catalog returns ``{name,kind,instantiate}`` with no
 * ``fields``; the unguarded form iterated ``blueprint.fields`` and threw
 * ``TypeError: e.fields is not iterable``, blanking the whole SPA (no error
 * boundary). These pin the normalized shape and that both the canonical and
 * the compat catalog render.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const apiMocks = vi.hoisted(() => ({
  getAutomationBlueprints: vi.fn(),
  instantiateAutomationBlueprint: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

import {
  AutomationBlueprints,
  normalizeBlueprint,
} from "./AutomationBlueprints";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor: condition never became true");
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

async function render() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(<AutomationBlueprints profile="default" />),
  );
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("normalizeBlueprint", () => {
  it("maps the canonical form schema", () => {
    const normalized = normalizeBlueprint({
      key: "daily-digest",
      title: "Daily digest",
      description: "A summary",
      tags: ["news"],
      fields: [{ name: "hour", type: "time", label: "Hour", options: [] }],
    });
    expect(normalized?.key).toBe("daily-digest");
    expect(normalized?.fields).toHaveLength(1);
    expect(normalized?.tags).toEqual(["news"]);
  });

  it("maps the compat {name,kind,instantiate} shape with no fields", () => {
    const normalized = normalizeBlueprint({
      name: "jobA",
      kind: "schedule-template",
      instantiate: "POST /api/cron/blueprints/instantiate",
    });
    expect(normalized).toEqual({
      key: "jobA",
      title: "jobA",
      description: "schedule-template",
      category: "",
      tags: [],
      fields: [],
      command: "POST /api/cron/blueprints/instantiate",
      appUrl: "",
    });
  });

  it("rejects malformed entries", () => {
    expect(normalizeBlueprint(null)).toBeNull();
    expect(normalizeBlueprint({ kind: "x" })).toBeNull();
  });
});

describe("AutomationBlueprints rendering", () => {
  it("renders the compat catalog without the fields TypeError", async () => {
    apiMocks.getAutomationBlueprints.mockResolvedValue({
      blueprints: [
        {
          name: "jobA",
          kind: "schedule-template",
          instantiate: "POST /api/cron/blueprints/instantiate",
        },
      ],
    });
    await render();
    await waitFor(() => container.textContent!.includes("jobA"));
    expect(container.textContent).toContain("schedule-template");
    expect(container.textContent).toContain("No configurable fields");
  });

  it("renders a canonical blueprint with its fields and Set up control", async () => {
    apiMocks.getAutomationBlueprints.mockResolvedValue({
      blueprints: [
        {
          key: "daily-digest",
          title: "Daily digest",
          description: "A summary",
          tags: ["news"],
          fields: [
            { name: "hour", type: "time", label: "Hour", options: [] },
          ],
        },
      ],
    });
    await render();
    await waitFor(() => container.textContent!.includes("Daily digest"));
    expect(container.textContent).toContain("news");
    const setup = [...container.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Set up",
    );
    expect(setup).not.toBeUndefined();
  });

  it("tolerates a response with no blueprints array", async () => {
    apiMocks.getAutomationBlueprints.mockResolvedValue({});
    await render();
    await waitFor(() =>
      container.textContent!.includes("No automation blueprints available."),
    );
  });
});
