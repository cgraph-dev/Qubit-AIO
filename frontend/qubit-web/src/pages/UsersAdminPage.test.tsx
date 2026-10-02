// @vitest-environment jsdom
/**
 * UsersAdminPage — single-operator adapter card is read-only, prefs get/set
 * round-trips font/theme, credentials wire link/unlink + model accounts +
 * catalog with the `qubit auth login` handoff; display-name/role/avatar/
 * email/GitHub/auth-start stay disabled with the exact prerequisite.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getOperatorSelf: vi.fn(),
  listOperators: vi.fn(),
  listMentionable: vi.fn(),
  getOperatorPrefs: vi.fn(),
  setOperatorPrefs: vi.fn(),
  listAuthLinks: vi.fn(),
  listModelAccounts: vi.fn(),
  selectModelAccount: vi.fn(),
  linkAuthProfile: vi.fn(),
  unlinkAuthProfile: vi.fn(),
  authConnectCatalog: vi.fn(),
}));

vi.mock("@/lib/ocFamilyAdminApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/ocFamilyAdminApi")>();
  return { ...actual, ...apiMocks };
});

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: timed out");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
  }
}

async function renderPage() {
  const [{ default: UsersAdminPage }] = await Promise.all([import("./UsersAdminPage")]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <MemoryRouter initialEntries={["/admin/users"]}>
      <UsersAdminPage />
    </MemoryRouter>
  );
  await act(async () => root.render(ui));
}

function byTestId(id: string): HTMLElement | null {
  return container.querySelector(`[data-testid="${id}"]`);
}

function setInput(el: HTMLElement | null, value: string) {
  if (!el) throw new Error("missing input");
  const input = el as HTMLInputElement;
  const proto = window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

beforeEach(() => {
  Object.values(apiMocks).forEach((fn) => fn.mockReset());
  apiMocks.getOperatorSelf.mockResolvedValue({
    state: "OK",
    profile: { id: "local-operator", displayName: "Qubit Operator", role: "operator" },
  });
  apiMocks.listOperators.mockResolvedValue({
    state: "OK",
    profiles: [{ id: "local-operator" }],
    count: 1,
  });
  apiMocks.listMentionable.mockResolvedValue({
    state: "OK",
    mentionable: [{ id: "local-operator" }],
    count: 1,
  });
  apiMocks.getOperatorPrefs.mockResolvedValue({
    state: "OK",
    entries: { font: "system", theme: "qubit" },
  });
  apiMocks.listAuthLinks.mockResolvedValue({ state: "OK", links: [], count: 0 });
  apiMocks.listModelAccounts.mockResolvedValue({ state: "OK", accounts: [], count: 0 });
  apiMocks.authConnectCatalog.mockResolvedValue({ state: "OK", providers: [], count: 0 });
  apiMocks.setOperatorPrefs.mockResolvedValue({
    state: "OK",
    applied: { font: "mono", theme: "qubit" },
    unsupported: [],
  });
  apiMocks.linkAuthProfile.mockResolvedValue({ state: "OK" });
  apiMocks.selectModelAccount.mockResolvedValue({ state: "OK" });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("UsersAdminPage", () => {
  it("labels the single-operator adapter and shows the read-only card", async () => {
    await renderPage();
    await waitFor(() => byTestId("users-operator-card") !== null);
    expect(byTestId("users-adapter-label")?.textContent).toContain(
      "Qubit single-operator adapter (no upstream users module)",
    );
    expect(byTestId("users-display-name")?.textContent).toContain("Qubit Operator");
    expect(byTestId("users-role")?.textContent).toContain("operator");
  });

  it("prefs set round-trips font/theme", async () => {
    await renderPage();
    await waitFor(() => byTestId("users-operator-card") !== null);
    setInput(byTestId("users-font"), "mono");
    await act(async () => {
      byTestId("users-prefs-set")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => apiMocks.setOperatorPrefs.mock.calls.length > 0);
    expect(apiMocks.setOperatorPrefs.mock.calls[0][0]).toEqual({
      font: "mono",
      theme: "qubit",
    });
  });

  it("link posts provider+keyRef and the handoff names `qubit auth login`", async () => {
    await renderPage();
    await waitFor(() => byTestId("users-operator-card") !== null);
    setInput(byTestId("users-link-provider"), "github");
    setInput(byTestId("users-link-keyref"), "env:GITHUB_TOKEN");
    await act(async () => {
      byTestId("users-link")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => apiMocks.linkAuthProfile.mock.calls.length > 0);
    expect(apiMocks.linkAuthProfile.mock.calls[0].slice(0, 2)).toEqual([
      "github",
      "env:GITHUB_TOKEN",
    ]);
    expect(byTestId("users-auth-handoff")?.textContent).toContain("qubit auth login");
  });

  it("summary stats render the real store counts", async () => {
    apiMocks.listOperators.mockResolvedValue({
      state: "OK",
      profiles: [{ id: "local-operator" }],
      count: 1,
    });
    apiMocks.listModelAccounts.mockResolvedValue({
      state: "OK",
      accounts: [{ profile_id: "p1" }],
      count: 1,
      active: "p1",
    });
    await renderPage();
    await waitFor(() => byTestId("users-stats") !== null);
    expect(byTestId("users-stat-operators")?.textContent).toContain("1");
    expect(byTestId("users-stat-accounts")?.textContent).toContain("1");
  });

  it("refresh re-reads the operator stores", async () => {
    await renderPage();
    await waitFor(() => byTestId("users-refresh") !== null);
    const callsBefore = apiMocks.getOperatorSelf.mock.calls.length;
    await act(async () => {
      byTestId("users-refresh")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => apiMocks.getOperatorSelf.mock.calls.length > callsBefore);
  });

  it("display-name/role/avatar/email/GitHub/auth-start are disabled with reasons", async () => {
    await renderPage();
    await waitFor(() => byTestId("users-operator-card") !== null);
    const { DISABLED_USERS_ACTIONS } = await import("@/lib/ocFamilyAdminApi");
    const actions = DISABLED_USERS_ACTIONS.map((d) => d.action);
    for (const want of [
      "users.setDisplayName",
      "users.setRole",
      "users.setAvatar",
      "users.linkEmail",
      "users.github.status",
      "users.authConnect.start",
    ]) {
      expect(actions).toContain(want);
      const row = byTestId(`ua-disabled-${want}`);
      expect(row).not.toBeNull();
      expect(row?.querySelector("button")?.disabled).toBe(true);
    }
    const roleRow = byTestId("ua-disabled-users.setRole");
    expect(roleRow?.textContent).toContain(
      "a multi-user profile/role store (Qubit is single-operator)",
    );
  });
});
