// @vitest-environment jsdom
/**
 * /canvas — roster, preview, confirm-gated add (staged-then-settle),
 * client-side traversal refusal, and the NEEDS-BRIDGE manifest.
 * `fetch` is stubbed (no backend); the shared api + nodes modules run for
 * real so the staged-confirmation round-trip is exercised end to end.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const CANVAS_LIST = {
  files: [
    { name: "index.html", path: "index.html", size_bytes: 42 },
    { name: "games/snake.html", path: "games/snake.html", size_bytes: 1337 },
  ],
  root: "state-home canvas/",
  live_reload: true,
  authority: "canvas host",
};

const NODES = {
  nodes: [{ id: "node-1", name: "alpha", state: "ready", manageable: true }],
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
    confirmation_id: "cid-canvas-1",
  };
  return {
    ok: false,
    status: 409,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const postCalls: string[] = [];
const postBodies: string[] = [];
function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/nodes/list")) return jsonResponse(NODES);
      if (u.includes("/canvas/get")) {
        return jsonResponse({ path: "index.html", content: "<!doctype html><h1>hi</h1>" });
      }
      if ((init?.method ?? "GET") === "POST" && u.includes("/canvas/")) {
        postCalls.push(u);
        postBodies.push(String(init?.body ?? ""));
        if (u.includes("confirmation_id=cid-canvas-1")) {
          return jsonResponse({ ok: true, state: "OK" });
        }
        return stagedResponse("Adding this canvas file needs confirmation.");
      }
      if (u.includes("/canvas/list")) return jsonResponse(CANVAS_LIST);
      return jsonResponse(CANVAS_LIST);
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

async function renderCanvasPage() {
  const { default: CanvasPage } = await import("./CanvasPage");
  await renderPage(<CanvasPage />);
  await waitFor(() => Boolean(container.querySelector('[aria-label="Canvas file roster"]')));
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function setTextarea(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function dialogButton(text: string): HTMLButtonElement | null {
  return (
    [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === text) ?? null
  );
}

beforeEach(() => {
  postCalls.length = 0;
  postBodies.length = 0;
  stubFetch();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("CanvasPage roster + preview", () => {
  it("renders the canvas-root file list with the live-reload indicator", async () => {
    await renderCanvasPage();
    expect(container.textContent).toContain("index.html");
    expect(container.textContent).toContain("games/snake.html");
    expect(container.querySelector('[aria-label="Live reload state"]')?.textContent).toMatch(
      /live reload on/,
    );
  });

  it("opens a file into the preview frame via the mirrored entry shape", async () => {
    await renderCanvasPage();
    const open = container.querySelector(
      '[aria-label="Open canvas file index.html"]',
    ) as HTMLButtonElement;
    await act(async () => open.click());
    await waitFor(() => Boolean(container.querySelector('[aria-label="Canvas preview"]')));
    const frame = container.querySelector('[aria-label="Canvas preview"]') as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toContain("/__qubit__/canvas/index.html");
  });
});

describe("CanvasPage confirm-gated add (staged-then-settle)", () => {
  it("stages on first POST, then settles after Confirm and reports completion", async () => {
    await renderCanvasPage();
    setInput(
      container.querySelector('[aria-label="New canvas file name"]') as HTMLInputElement,
      "hello.html",
    );
    setTextarea(
      container.querySelector('[aria-label="New canvas file content"]') as HTMLTextAreaElement,
      "<h1>hi</h1>",
    );
    const add = container.querySelector('[aria-label="Add canvas file"]') as HTMLButtonElement;
    await act(async () => add.click());

    await waitFor(() => Boolean(dialogButton("Confirm")));
    expect(postCalls.filter((u) => u.includes("/canvas/add")).length).toBe(1);

    await act(async () => dialogButton("Confirm")!.click());
    await waitFor(() => (container.textContent ?? "").includes("Add completed."));
    expect(postCalls.filter((u) => u.includes("/canvas/add")).length).toBe(2);
  });
});

describe("CanvasPage client-side traversal refusal", () => {
  it("refuses ../ names without issuing any mutation", async () => {
    await renderCanvasPage();
    const before = postCalls.length;
    setInput(
      container.querySelector('[aria-label="New canvas file name"]') as HTMLInputElement,
      "../evil.html",
    );
    const add = container.querySelector('[aria-label="Add canvas file"]') as HTMLButtonElement;
    await act(async () => add.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("not a safe canvas-root name"),
    );
    expect(postCalls.length).toBe(before);
  });

  it("refuses absolute paths without issuing any mutation", async () => {
    await renderCanvasPage();
    const before = postCalls.length;
    setInput(
      container.querySelector('[aria-label="New canvas file name"]') as HTMLInputElement,
      "/etc/passwd",
    );
    const add = container.querySelector('[aria-label="Add canvas file"]') as HTMLButtonElement;
    await act(async () => add.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("not a safe canvas-root name"),
    );
    expect(postCalls.length).toBe(before);
  });
});

describe("ocFamilyCanvasApi ROUTES manifest", () => {
  it("lists every path under /api/qubit/canvas/<action>, all NEEDS-BRIDGE", async () => {
    const { ROUTES } = await import("@/lib/ocFamilyCanvasApi");
    expect(ROUTES.length).toBeGreaterThan(0);
    for (const r of ROUTES) {
      expect(r.path.startsWith("/api/qubit/canvas/")).toBe(true);
      expect(["GET", "POST"]).toContain(r.method);
      expect(r.bridge).toBe("NEEDS-BRIDGE");
    }
    const paths = ROUTES.map((r) => `${r.method} ${r.path}`);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("refuses traversal names purely (no fetch involved)", async () => {
    const { isCanvasNameSafe, resolveCanvasPreviewUrl } = await import(
      "@/lib/ocFamilyCanvasApi"
    );
    expect(isCanvasNameSafe("../evil.html")).toBe(false);
    expect(isCanvasNameSafe("/etc/passwd")).toBe(false);
    expect(isCanvasNameSafe("")).toBe(false);
    expect(isCanvasNameSafe("index.html")).toBe(true);
    expect(isCanvasNameSafe("games/snake.html")).toBe(true);
    expect(resolveCanvasPreviewUrl("https://evil.example/x")).toBeUndefined();
    expect(resolveCanvasPreviewUrl("/etc/passwd")).toBeUndefined();
    expect(resolveCanvasPreviewUrl("/__qubit__/canvas/index.html")).toBe(
      "/__qubit__/canvas/index.html",
    );
  });
});
