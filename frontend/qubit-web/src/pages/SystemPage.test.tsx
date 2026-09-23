// @vitest-environment jsdom
/**
 * /system e-stop — the dashboard's emergency-stop control must read and
 * mutate the ONE shared power latch (`/api/power`), never a legacy 404 path.
 *
 * This pins the control to the canonical endpoint: the badge reflects the
 * live `control.emergency_stop`, latching goes through a confirm dialog, and
 * resetting uses `action:"reset"`.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getSystemStats: vi.fn(),
  getMemory: vi.fn(),
  getCredentialPool: vi.fn(),
  getCheckpoints: vi.fn(),
  getHooks: vi.fn(),
  getCurator: vi.fn(),
  getPortal: vi.fn(),
  checkQubitUpdate: vi.fn(),
  getGatewayStatus: vi.fn(),
  getPowerStatus: vi.fn(),
  powerAction: vi.fn(),
  startGateway: vi.fn(),
  stopGateway: vi.fn(),
  restartGateway: vi.fn(),
  getActionStatus: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  parsePolicyRefusal: () => null,
  policyRefusalMessage: () => "",
}));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));

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

function powerStatus(emergency_stop: boolean, global_disable = false) {
  return {
    state: "OK",
    control: {
      state: "OK",
      emergency_stop,
      global_disable,
      persisted: true,
      max_concurrency: 4,
      active: 0,
    },
  };
}

async function renderSystem() {
  const [
    { default: SystemPage },
    { I18nProvider },
    { PageHeaderProvider },
  ] = await Promise.all([
    import("./SystemPage"),
    import("@/i18n"),
    import("@/contexts/PageHeaderProvider"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter initialEntries={["/system"]}>
          <PageHeaderProvider pluginTabs={[]}>
            <SystemPage />
          </PageHeaderProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() => container.textContent!.includes("Power control"));
}

function buttonByText(text: string): HTMLButtonElement | null {
  // Confirm dialogs portal into document.body, outside `container`. Exact
  // match so "Emergency stop" does not also match the dialog's
  // "Latch emergency stop" action.
  return (
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getStatus.mockResolvedValue({
    can_update_qubit: false,
    active_sessions: 0,
  });
  apiMocks.getSystemStats.mockResolvedValue({ os: "linux", arch: "x64" });
  apiMocks.getMemory.mockResolvedValue({
    active: "",
    providers: [],
    builtin_files: { memory: 0, user: 0 },
  });
  apiMocks.getCredentialPool.mockResolvedValue({ providers: [] });
  apiMocks.getCheckpoints.mockResolvedValue({ sessions: [], total_bytes: 0 });
  apiMocks.getHooks.mockResolvedValue({ hooks: [], valid_events: [] });
  apiMocks.getCurator.mockResolvedValue({ enabled: false, paused: false });
  apiMocks.getPortal.mockResolvedValue({ logged_in: false, features: [] });
  apiMocks.checkQubitUpdate.mockRejectedValue(new Error("operator-owned"));
  apiMocks.getGatewayStatus.mockResolvedValue({ supervision: "" });
  apiMocks.getPowerStatus.mockResolvedValue(powerStatus(false));
  apiMocks.powerAction.mockResolvedValue({ state: "OK", health: "degraded" });
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("ResizeObserver", class { disconnect() {} observe() {} unobserve() {} });
  vi.stubGlobal("matchMedia", () => ({
    addEventListener() {},
    matches: false,
    media: "",
    removeEventListener() {},
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("SystemPage gateway control confirmation", () => {
  it("routes a Restart through the shared confirmation round-trip", async () => {
    // Restart passes api.restartGateway a confirm callback. Invoking it is the
    // human decision; here the api mock calls it immediately to prove the
    // System page owns that round-trip (the old no-confirm POST staged a 409).
    apiMocks.restartGateway.mockImplementation(
      async (confirm?: (detail: string) => boolean | Promise<boolean>) => {
        expect(typeof confirm).toBe("function");
        const ok = await confirm!("gateway restart changes a service");
        expect(ok).toBe(true);
        return { ok: true, name: "gateway-restart", state: "RESTARTING" };
      },
    );
    await renderSystem();

    const restart = buttonByText("Restart");
    expect(restart).not.toBeNull();
    await act(async () => restart!.click());

    // The in-app ConfirmDialog (not window.confirm) collects the decision.
    await waitFor(() => Boolean(buttonByText("Restart gateway")));
    await act(async () => buttonByText("Restart gateway")!.click());

    await waitFor(() => apiMocks.restartGateway.mock.calls.length > 0);
    expect(apiMocks.restartGateway).toHaveBeenCalledTimes(1);
  });

  it("declining the confirmation does not claim success", async () => {
    let decision: boolean | null = null;
    apiMocks.restartGateway.mockImplementation(
      async (confirm?: (detail: string) => boolean | Promise<boolean>) => {
        decision = await confirm!("gateway restart changes a service");
        if (!decision) throw new Error("CONFIRM_REQUIRED");
        return { ok: true, name: "gateway-restart" };
      },
    );
    await renderSystem();

    await act(async () => buttonByText("Restart")!.click());
    await waitFor(() => Boolean(buttonByText("Cancel")));
    // Cancel is the dialog's dismiss action.
    const cancel = [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Cancel",
    );
    await act(async () => cancel!.click());

    await waitFor(() => decision !== null);
    expect(decision).toBe(false);
  });
});

describe("SystemPage emergency stop", () => {
  it("reads the live latch and latches through /api/power after confirmation", async () => {
    apiMocks.getPowerStatus
      .mockResolvedValueOnce(powerStatus(false))
      .mockResolvedValueOnce(powerStatus(true));
    await renderSystem();

    const estop = buttonByText("Emergency stop");
    expect(estop).not.toBeNull();

    await act(async () => estop!.click());
    await waitFor(() => Boolean(buttonByText("Latch emergency stop")));

    const confirmBtn = buttonByText("Latch emergency stop")!;
    await act(async () => confirmBtn.click());
    await waitFor(() =>
      apiMocks.powerAction.mock.calls.some((c) => c[0] === "emergency-stop"),
    );

    expect(apiMocks.powerAction).toHaveBeenCalledWith("emergency-stop");
  });

  it("offers reset (not latch) when the stop is already latched", async () => {
    apiMocks.getPowerStatus.mockResolvedValue(powerStatus(true));
    apiMocks.powerAction.mockResolvedValue({ state: "OK", health: "disabled" });
    await renderSystem();

    await waitFor(() => Boolean(buttonByText("Reset emergency stop")));
    expect(buttonByText("Emergency stop")).toBeNull();

    await act(async () => buttonByText("Reset emergency stop")!.click());
    await waitFor(() =>
      apiMocks.powerAction.mock.calls.some((c) => c[0] === "reset"),
    );
    expect(apiMocks.powerAction).toHaveBeenCalledWith("reset");
  });
});
