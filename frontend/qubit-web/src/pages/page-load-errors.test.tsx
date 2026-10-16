// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { probePage } from "@/test-utils/pageResilience";
import { api } from "@/lib/api";
import ConfigPage from "./ConfigPage";
import EnvPage from "./EnvPage";
import SessionsPage from "./SessionsPage";
import { PageErrorBoundary } from "@/components/PageErrorBoundary";

vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
afterEach(() => vi.restoreAllMocks());
describe("page load errors", () => {
  for (const [name, Page, method] of [
    ["configuration", ConfigPage, "getConfig"],
    ["keys", EnvPage, "getEnvVars"],
    ["sessions", SessionsPage, "getSessions"],
  ] as const) {
    it(`${name} exposes a retry after an API failure`, async () => {
      vi.spyOn(api, method).mockRejectedValue(new Error("offline"));
      const probe = await probePage(Page);
      try {
        expect(probe.result.threw).toBe(false);
        expect(probe.result.text).toContain(`Failed to load ${name}.`);
        expect(probe.result.text).toContain("Retry");
        const before = vi.mocked(api[method]).mock.calls.length;
        await act(async () => {
          const retry = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Retry");
          retry!.click();
        });
        await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
        expect(vi.mocked(api[method]).mock.calls.length).toBeGreaterThan(before);
      } finally { await probe.unmount(); }
    });
  }
  it("contains a render crash and retries its child", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let crash = true;
    function Child() { if (crash) throw new Error("broken"); return <p>Recovered</p>; }
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
      await act(async () => { root.render(<PageErrorBoundary><Child /></PageErrorBoundary>); });
      expect(host.textContent).toContain("failed to render");
      crash = false;
      await act(async () => { host.querySelector("button")!.click(); });
      expect(host.textContent).toBe("Recovered");
    } finally { await act(async () => root.unmount()); }
  });
});
