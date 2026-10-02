// @vitest-environment jsdom
/**
 * /nodes — list/describe plus confirm-gated enroll/approve/revoke/remove,
 * capabilities refresh, dispatch, and rename (with the re-enroll warning).
 * Confirm-gated buttons assert staged-then-settle through the in-page dialog;
 * unmanageable rows assert disabled-with-reason, never firing.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const NODES = {
  nodes: [
    { id: "node-a", name: "alpha", state: "ready", manageable: true },
    {
      id: "node-b",
      name: "beta",
      state: "pending",
      manageable: false,
      manage_reason: "Approval is owned by the node operator.",
    },
  ],
  authority: "read-only node roster",
};

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function stagedResponse(detail: string) {
  const body = {
    state: "NEEDS_CONFIRMATION",
    code: "CONFIRM_REQUIRED",
    detail,
    confirmation_id: "cid-1",
  };
  return {
    ok: false,
    status: 409,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const postCalls: string[] = [];
function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/nodes/describe")) {
        return jsonResponse({
          node: { id: "node-a", name: "alpha", state: "ready", capabilities: ["run"] },
        });
      }
      if (u.includes("/nodes/capabilities")) {
        return jsonResponse({ node_id: "node-a", capabilities: ["run", "fetch"] });
      }
      if ((init?.method ?? "GET") === "POST") {
        postCalls.push(u);
        // Settle re-issue carries the staged id; first attempt stages.
        if (u.includes("confirmation_id=cid-1")) {
          return jsonResponse({ ok: true, node_id: "node-a", state: "ready" });
        }
        return stagedResponse("Approving this node needs confirmation.");
      }
      return jsonResponse(NODES);
    }),
  );
}

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

async function renderPage(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderNodesPage() {
  const { default: NodesPage } = await import("./NodesPage");
  await renderPage(<NodesPage />);
  await waitFor(() => Boolean(container.querySelector('[aria-label="Node roster"]')));
}

function dialogButton(text: string): HTMLButtonElement | null {
  return (
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

beforeEach(() => {
  postCalls.length = 0;
  stubFetch();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("NodesPage roster + describe", () => {
  it("renders the node roster", async () => {
    await renderNodesPage();
    expect(container.textContent).toContain("node-a");
    expect(container.textContent).toContain("node-b");
    expect(container.textContent).not.toMatch(/relay/i);
  });

  it("renders unmanageable rows disabled with their reason and never fires", async () => {
    await renderNodesPage();
    const refused = container.querySelector('[data-state="refused"]');
    expect(refused).not.toBeNull();
    expect(refused?.getAttribute("aria-disabled")).toBe("true");
    expect(refused?.getAttribute("title")).toContain("node operator");
    const before = postCalls.length;
    await act(async () => {
      (refused as HTMLButtonElement).click();
    });
    expect(postCalls.length).toBe(before);
  });

  it("describes a node and refreshes capabilities", async () => {
    await renderNodesPage();
    const describe = container.querySelector(
      '[aria-label="Describe node node-a"]',
    ) as HTMLButtonElement;
    await act(async () => describe.click());
    await waitFor(() => (container.textContent ?? "").includes("capabilities: run"));
    const refresh = container.querySelector(
      '[aria-label="Refresh capabilities for node-a"]',
    ) as HTMLButtonElement;
    await act(async () => refresh.click());
    await waitFor(() => (container.textContent ?? "").includes("run, fetch"));
  });

  it("shows the rename re-enroll warning", async () => {
    await renderNodesPage();
    const describe = container.querySelector(
      '[aria-label="Describe node node-a"]',
    ) as HTMLButtonElement;
    await act(async () => describe.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("may require re-enrollment"),
    );
  });

  it("mirrors the upstream card hierarchy (enroll card, roster card, live-link rows)", async () => {
    await renderNodesPage();
    expect(container.querySelector('[aria-label="Enroll node form"]')).not.toBeNull();
    const card = container.querySelector('[aria-label="Node roster card"]');
    expect(card).not.toBeNull();
    // The card header shows the backend authority when present, else the
    // live-links subtitle.
    expect(card?.textContent).toMatch(/live links|read-only node roster/);
    expect(card?.querySelector('[aria-label="Node roster"]')).not.toBeNull();
  });
});

describe("NodesPage confirm-gated approve (staged-then-settle)", () => {
  it("stages on first POST, then settles after Confirm", async () => {
    await renderNodesPage();
    const approve = container.querySelector(
      '[aria-label="Approve node node-a"]',
    ) as HTMLButtonElement;
    await act(async () => approve.click());

    await waitFor(() => Boolean(dialogButton("Confirm")));
    expect(postCalls.length).toBe(1);
    // The dialog carries the backend stage detail.
    expect(
      [...document.querySelectorAll('[role="dialog"]')].some((d) =>
        (d.textContent ?? "").includes("Approving this node needs confirmation."),
      ),
    ).toBe(true);

    await act(async () => dialogButton("Confirm")!.click());
    await waitFor(() => (container.textContent ?? "").includes("Approve completed."));
    expect(postCalls.length).toBe(2);
  });

  it("cancelling the dialog reports the failure without settling", async () => {
    await renderNodesPage();
    const approve = container.querySelector(
      '[aria-label="Approve node node-a"]',
    ) as HTMLButtonElement;
    await act(async () => approve.click());
    await waitFor(() => Boolean(dialogButton("Cancel")));
    await act(async () => dialogButton("Cancel")!.click());
    await waitFor(() => (container.textContent ?? "").includes("Approve failed."));
    expect(postCalls.length).toBe(1);
  });
});
