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
  const { default: SessionOpsPage } = await import("./SessionOpsPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<SessionOpsPage />);
  });
}

beforeEach(() => {
  apiMocks.fetchJSON.mockReset();
  apiMocks.fetchJSON.mockImplementation(async (url: string) => {
    if (url.startsWith("/api/sessions")) {
      return { sessions: [{ id: "s1", title: "First" }], total: 1 };
    }
    if (url === "/api/qubit/session-ops/groups") return { groups: [] };
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

describe("SessionOpsPage", () => {
  it("states the fork / branch-switch / verify-only distinction", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[data-testid="session-ops-page"]') !== null);
    const guidance = document
      .querySelector('[aria-label="fork-versus-branch-guidance"]')
      ?.textContent;
    expect(guidance).toMatch("into a NEW session");
    expect(guidance).toMatch("restores in place");
    expect(guidance).toMatch("verify-only");
  });

  it("stages fork behind confirm, then settles with the new session", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/sessions")) {
        return { sessions: [{ id: "s1", title: "First" }], total: 1 };
      }
      if (url === "/api/qubit/session-ops/groups") return { groups: [] };
      if (url === "/api/qubit/session-ops/fork") {
        throw new ConfirmRequiredError("confirm fork of s1", "stage-fork-1", async () => ({
          ok: true,
          new_session: "s2",
        }));
      }
      throw new Error(`unexpected: ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="pick-session-s1"]') !== null);
    await act(async () => {
      click("pick-session-s1");
    });
    await act(async () => {
      click("session-op-fork");
    });
    // Staged: the confirm step appears before anything settles.
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')).toBeNull();
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')?.textContent).toMatch("s2");
    expect(
      apiMocks.fetchJSON.mock.calls.some((c) => String(c[0]) === "/api/qubit/session-ops/fork"),
    ).toBe(true);
  });

  it("cancel leaves the op unsettled", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="pick-session-s1"]') !== null);
    await act(async () => {
      click("pick-session-s1");
    });
    await act(async () => {
      click("session-op-compact");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    await act(async () => {
      click("cancel-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') === null);
    expect(
      apiMocks.fetchJSON.mock.calls.some((c) =>
        String(c[0]).startsWith("/api/qubit/session-ops/compact"),
      ),
    ).toBe(false);
  });
});
