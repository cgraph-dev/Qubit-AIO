// @vitest-environment jsdom
/**
 * /computer — the ported agent computer console.
 *
 * Real backend wiring: computer-status (200), desktop/ops (200),
 * desktop/presentation (200), desktop/present (409 staged), agent-workspace
 * roster (200). The upstream `/api/computer/*` VM family does not exist and
 * must render as an honest, stated gap — never a dead-but-live-looking button.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const STATUS = {
  state: "OK",
  control: {
    state: "UNAVAILABLE",
    code: "COMPUTER_USE_NO_DRIVER",
    session: "x11",
    atspi_reachable: true,
    actuator: "none",
    detail: "no accessibility actuator resolved and no driver present",
  },
};

const OPS = {
  ok: true,
  result: {
    desktop: {
      state: "OK",
      schema: "qubit-computer-desktop-op/1",
      ops: ["desktop.launch", "desktop.picture_in_picture", "desktop.present"],
      apps: ["browser", "editor", "terminal", "vscode"],
      presentation_modes: ["document", "screen"],
    },
  },
};

const PRESENTATION = {
  ok: true,
  result: {
    desktop: {
      state: "OK",
      presenting: false,
      mode: "",
      panes: [],
      rollback: "read-only; nothing to roll back",
    },
  },
};

const ROSTER = {
  state: "OK",
  agentId: "qubit-worker",
  entries: [
    { path: "agent/main.py", name: "main.py", kind: "file", size: 120 },
    { path: "agent/sub", name: "sub", kind: "dir", size: 0 },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const fetchCalls: string[] = [];
function stubFetch(impl: (url: string, init?: RequestInit) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      fetchCalls.push(String(url));
      return impl(url, init);
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

async function renderComputer() {
  const { default: Page } = await import("./ComputerWorkspacePage");
  await renderPage(<Page />);
  // Initial tab is Browser; the live actuator status renders in the shell.
  await waitFor(() => (container.textContent ?? "").includes("Browser offline"));
}

beforeEach(() => {
  fetchCalls.length = 0;
  stubFetch((url) => {
    if (url.includes("computer-status")) return jsonResponse(STATUS);
    if (url.includes("/desktop/ops")) return jsonResponse(OPS);
    if (url.includes("/desktop/presentation")) return jsonResponse(PRESENTATION);
    if (url.includes("agent-workspace/list")) return jsonResponse(ROSTER);
    return jsonResponse({ state: "OK" });
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("ComputerWorkspacePage", () => {
  it("renders the live actuator status", async () => {
    await renderComputer();
    const terminal = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Terminal"),
    ) as HTMLButtonElement;
    await act(async () => terminal.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("Actuator offline"),
    );
    expect(container.textContent).toContain("COMPUTER_USE_NO_DRIVER");
  });

  it("states the interactive shell gap instead of faking a command run", async () => {
    await renderComputer();
    const terminal = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Terminal"),
    ) as HTMLButtonElement;
    expect(terminal).toBeTruthy();
    await act(async () => terminal.click());
    // Copy updated (om-computer-workspace.tsx:387): the shell gap is stated as
    // "deliberately out of scope" with the exact audited desktop-control
    // boundary, still never faking a command run.
    expect(container.textContent).toContain(
      "Shell execution is deliberately out of scope",
    );
    expect(container.textContent).toContain(
      "nothing here pretends a command ran",
    );
  });

  it("stages a desktop presentation and surfaces the confirmation", async () => {
    await renderComputer();
    stubFetch((url) => {
      if (url.includes("/desktop/present")) {
        return jsonResponse(
          {
            ok: false,
            state: "NEEDS_CONFIRMATION",
            code: "CONFIRM_REQUIRED",
            detail: "Present the Control UI: say yes to confirm.",
            confirmation_id: "018d5da1b93e4fe5855bf46b2aff2d1c",
          },
          409,
        );
      }
      if (url.includes("computer-status")) return jsonResponse(STATUS);
      if (url.includes("/desktop/ops")) return jsonResponse(OPS);
      if (url.includes("/desktop/presentation")) return jsonResponse(PRESENTATION);
      if (url.includes("agent-workspace/list")) return jsonResponse(ROSTER);
      return jsonResponse({ state: "OK" });
    });
    const terminal = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Terminal"),
    ) as HTMLButtonElement;
    await act(async () => terminal.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("Present desktop"),
    );
    const present = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Present desktop"),
    ) as HTMLButtonElement;
    await act(async () => present.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("staged for confirmation"),
    );
  });

  it("never renders a fake browser-session create button as live", async () => {
    await renderComputer();
    const open = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Open a browser session"),
    ) as HTMLButtonElement;
    expect(open).toBeTruthy();
    expect(open.disabled).toBe(true);
    expect(container.textContent).toContain("no session-create backend");
  });

  it("browses the real agent workspace roster on the Files tab", async () => {
    await renderComputer();
    const files = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Files"),
    ) as HTMLButtonElement;
    await act(async () => files.click());
    await waitFor(() => (container.textContent ?? "").includes("agent/main.py"));
    expect(container.textContent).toContain("Directories cannot be opened");
  });
});
