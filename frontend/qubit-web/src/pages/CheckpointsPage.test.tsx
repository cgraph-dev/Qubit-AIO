// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({ fetchJSON: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, fetchJSON: apiMocks.fetchJSON };
});

import { ConfirmRequiredError } from "@/lib/api";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

function click(label: string) {
  const el = document.querySelector(`button[aria-label="${label}"]`);
  if (!el) throw new Error(`button not rendered: ${label}`);
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

async function renderPage() {
  const { default: CheckpointsPage } = await import("./CheckpointsPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<CheckpointsPage />);
  });
}

beforeEach(() => {
  apiMocks.fetchJSON.mockReset();
  apiMocks.fetchJSON.mockImplementation(async (url: string) => {
    if (url === "/api/ops/checkpoints") {
      return { sessions: [{ session: "s1", files: 2, bytes: 100 }], total_bytes: 100 };
    }
    throw new Error(`unexpected GET: ${url}`);
  });
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container?.remove();
  vi.resetModules();
});

describe("CheckpointsPage", () => {
  it("states verify-only and renders no restore button", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[data-testid="checkpoints-page"]') !== null);
    const notice = document.querySelector('[aria-label="verify-only-notice"]')?.textContent ?? "";
    expect(notice).toMatch("verify-only");
    expect(notice).toMatch("no restore button");
    const restoreButtons = [...document.querySelectorAll("button")].filter((b) =>
      (b.getAttribute("aria-label") ?? "").includes("restore"),
    );
    expect(restoreButtons).toHaveLength(0);
  });

  it("verifies a journal checkpoint directly (no confirm gate)", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string) => {
      if (url === "/api/ops/checkpoints") {
        return { sessions: [{ session: "s1", files: 2, bytes: 100 }], total_bytes: 100 };
      }
      if (url.startsWith("/api/qubit/checkpoints/verify")) {
        return { ok: true, checkpoint: "s1", message_count: 5 };
      }
      throw new Error(`unexpected: ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="checkpoint-verify-submit"]') !== null);
    await act(async () => {
      click("checkpoint-verify-s1");
    });
    await act(async () => {
      click("checkpoint-verify-submit");
    });
    await waitFor(() => document.querySelector('[aria-label="checkpoint-verify-output"]') !== null);
    expect(document.querySelector('[aria-label="checkpoint-verify-output"]')?.textContent).toMatch(
      "passed",
    );
  });

  it("stages prune behind confirm, then settles", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/ops/checkpoints") {
        return { sessions: [{ session: "s1", files: 2, bytes: 100 }], total_bytes: 100 };
      }
      if (url === "/api/ops/checkpoints/prune") {
        expect(init?.method).toBe("POST");
        throw new ConfirmRequiredError("confirm prune", "stage-prune-1", async () => ({
          ok: true,
          pruned: 1,
        }));
      }
      throw new Error(`unexpected: ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="checkpoint-prune-submit"]') !== null);
    await act(async () => {
      click("checkpoint-prune-submit");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')).toBeNull();
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
    expect(
      apiMocks.fetchJSON.mock.calls.some((c) => String(c[0]) === "/api/ops/checkpoints/prune"),
    ).toBe(true);
  });

  it("links compaction branch/restore to the session-ops page", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="compaction-links"]') !== null);
    const hrefs = [...document.querySelectorAll('[aria-label="compaction-links"] a')].map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/session-ops");
  });
});
