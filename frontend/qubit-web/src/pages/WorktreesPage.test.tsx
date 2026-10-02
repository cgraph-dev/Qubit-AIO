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

function setInput(label: string, value: string) {
  const el = document.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement | null;
  if (!el) throw new Error(`input not rendered: ${label}`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function renderPage() {
  const { default: WorktreesPage } = await import("./WorktreesPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<WorktreesPage />);
  });
}

beforeEach(() => {
  apiMocks.fetchJSON.mockReset();
  apiMocks.fetchJSON.mockImplementation(async (url: string) => {
    if (url === "/api/git/worktrees") {
      return { worktrees: [{ path: "/repo/wt1", branch: "b1" }] };
    }
    if (url === "/api/git/branches") return { branches: ["main", "b1"] };
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

describe("WorktreesPage", () => {
  it("labels restore and prune with their honest typed codes", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[data-testid="worktrees-page"]') !== null);
    const body = document.querySelector('[data-testid="worktrees-page"]')?.textContent ?? "";
    expect(body).toMatch("WORKTREES_RESTORE_LOCAL_READD");
    expect(body).toMatch("WORKTREES_GC_LOCAL_PRUNE");
    expect(body).toMatch("SAFE_EQUIVALENT");
    expect(body).toMatch("never presented as an upstream snapshot restore");
  });

  it("lists worktrees and branches from the reused read routes", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="worktree-remove-/repo/wt1"]') !== null);
    const list = document.querySelector('[aria-label="worktree-list"]')?.textContent ?? "";
    expect(list).toMatch("/repo/wt1");
    const branches = document.querySelector('[aria-label="worktree-branches"]')?.textContent ?? "";
    expect(branches).toMatch("main");
  });

  it("stages create behind confirm, then settles", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/git/worktrees") {
        return { worktrees: [] as unknown[] };
      }
      if (url === "/api/git/branches") return { branches: ["main"] };
      if (url === "/api/qubit/worktrees/create") {
        expect(init?.method).toBe("POST");
        throw new ConfirmRequiredError("confirm create", "stage-create-1", async () => ({
          ok: true,
          path: "/repo/wt2",
        }));
      }
      throw new Error(`unexpected: ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="worktree-create-submit"]') !== null);
    await act(async () => {
      setInput("worktree-new-path", "/repo/wt2");
      setInput("worktree-new-branch", "b2");
    });
    await act(async () => {
      click("worktree-create-submit");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')).toBeNull();
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')?.textContent).toMatch("/repo/wt2");
  });

  it("stages prune behind confirm, then settles", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string) => {
      if (url === "/api/git/worktrees") {
        return { worktrees: [] as unknown[] };
      }
      if (url === "/api/git/branches") return { branches: ["main"] };
      if (url === "/api/qubit/worktrees/prune") {
        throw new ConfirmRequiredError("confirm prune", "stage-prune-1", async () => ({
          ok: true,
          pruned: 2,
        }));
      }
      throw new Error(`unexpected: ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="worktree-prune-submit"]') !== null);
    await act(async () => {
      click("worktree-prune-submit");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
    expect(
      apiMocks.fetchJSON.mock.calls.some((c) => String(c[0]) === "/api/qubit/worktrees/prune"),
    ).toBe(true);
  });
});
