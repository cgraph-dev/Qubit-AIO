// @vitest-environment jsdom
/** The helper-liveness hook: `/qubit-chat` yields Right-Ctrl only when the
 *  system-wide desktop helper is enabled AND running. */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDesktopVoiceHelper } from "./useDesktopVoiceHelper";

const mocks = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock("@/lib/voiceEdit", () => ({ voiceEdit: { status: mocks.status } }));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Probe({ active }: { active: boolean }) {
  const live = useDesktopVoiceHelper(active, 10000);
  return <span data-testid="v">{String(live)}</span>;
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.clearAllMocks();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useDesktopVoiceHelper", () => {
  it("is true only when enabled and the helper is live", async () => {
    mocks.status.mockResolvedValue({ enabled: true, helper_live: true });
    await act(async () => root.render(<Probe active />));
    await flush();
    expect(container.querySelector('[data-testid="v"]')?.textContent).toBe("true");
  });

  it("is false when enabled but the helper is not running", async () => {
    mocks.status.mockResolvedValue({ enabled: true, helper_live: false });
    await act(async () => root.render(<Probe active />));
    await flush();
    expect(container.querySelector('[data-testid="v"]')?.textContent).toBe("false");
  });

  it("is false on a status error", async () => {
    mocks.status.mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<Probe active />));
    await flush();
    expect(container.querySelector('[data-testid="v"]')?.textContent).toBe("false");
  });
});
