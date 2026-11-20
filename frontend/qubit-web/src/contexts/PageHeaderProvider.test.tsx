// @vitest-environment jsdom
import { act, useLayoutEffect } from "react";
import { createRoot } from "react-dom/client";
import { Link, MemoryRouter, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { PageHeaderProvider } from "./PageHeaderProvider";
import { usePageHeader } from "./usePageHeader";

vi.mock("@/i18n", () => ({ useI18n: () => ({ t: { app: { brand: "Qubit" } } }) }));

function CurrentRoute() {
  const { pathname } = useLocation();
  const { setTitle, setAfterTitle } = usePageHeader();
  useLayoutEffect(() => {
    if (pathname === "/plain") return;
    setTitle(`Title ${pathname}`);
    setAfterTitle(<button type="button">Action {pathname}</button>);
    return () => {
      setTitle(null);
      setAfterTitle(null);
    };
  }, [pathname, setTitle, setAfterTitle]);

  return <><Link to="/next">Next</Link><Link to="/plain">Plain</Link></>;
}

describe("dashboard header slots", () => {
  it("keeps controls registered on mount and during same-commit route changes", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    try {
      await act(async () => root.render(
        <MemoryRouter initialEntries={["/first"]}>
          <PageHeaderProvider pluginTabs={[]}><CurrentRoute /></PageHeaderProvider>
        </MemoryRouter>,
      ));
      const header = host.querySelector("[data-dashboard-page-header]");
      expect(header?.textContent).toBe("Title /firstAction /first");
      const main = host.querySelector("main")!;
      main.scrollTop = 350;
      await act(async () => host.querySelector<HTMLAnchorElement>("a[href='/next']")?.click());
      expect(header?.textContent).toBe("Title /nextAction /next");
      expect(main.scrollTop).toBe(0);
      await act(async () => host.querySelector<HTMLAnchorElement>("a[href='/plain']")?.click());
      expect(header?.textContent).toBe("Plain");
    } finally {
      await act(async () => root.unmount());
      host.remove();
      vi.unstubAllGlobals();
    }
  });
});
