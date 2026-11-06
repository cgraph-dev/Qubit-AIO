import { describe, expect, it, vi } from "vitest";
import { createElement, type ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { renderToStaticMarkup } from "react-dom/server";
import App from "./App";

// Manifests the mocked usePlugins supplies. `vi.mock` is hoisted, so this must
// be declared with `vi.hoisted` to be visible inside the factory.
const { mockManifests } = vi.hoisted(() => ({
  mockManifests: [] as Array<{
    name: string;
    label: string;
    icon: string;
    tab: { path: string; hidden?: boolean; position?: string; override?: string };
  }>,
}));

vi.mock("@/plugins", () => ({
  PluginPage: ({ name }: { name: string }) =>
    createElement("div", { "data-testid": "plugin-page", "data-plugin": name }),
  PluginSlot: () => null,
  usePlugins: () => ({ manifests: mockManifests, loading: false }),
}));
vi.mock("@/i18n", () => ({ useI18n: () => ({ t: { app: { openNavigation: "Open navigation", navigation: "Navigation", brand: "Qubit", closeNavigation: "Close navigation", pluginNavSection: "Plugins", nav: { chat: "Chat", sessions: "Sessions", analytics: "Analytics", models: "Models", cron: "Cron", skills: "Skills", profiles: "Profiles", config: "Config", keys: "Keys", documentation: "Documentation" } }, common: { expand: "Expand", collapse: "Collapse" }, status: { gateway: "Gateway" }, theme: { switchTheme: "Switch theme" }, language: { switchTo: "Language" } } }) }));
vi.mock("@/themes", () => ({ useTheme: () => ({ theme: {} }) }));
vi.mock("@/hooks/useSidebarStatus", () => ({ useSidebarStatus: () => null }));
vi.mock("@/contexts/useSystemActions", () => ({ useSystemActions: () => ({}) }));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({}) }));
vi.mock("@/contexts/ProfileProvider", () => ({ ProfileProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/contexts/PageHeaderProvider", () => ({ PageHeaderProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/components/SidebarFooter", () => ({ SidebarFooter: () => null }));
vi.mock("@/components/SidebarStatusStrip", () => ({ SidebarStatusStrip: () => null, gatewayLine: () => ({ tone: "text-success", label: "ok" }) }));
vi.mock("@/components/AuthWidget", () => ({ AuthWidget: () => null }));
vi.mock("@/components/ProfileSwitcher", () => ({ ProfileSwitcher: () => null }));
vi.mock("@/components/ProfileScopeBanner", () => ({ ProfileScopeBanner: () => null }));
vi.mock("@/components/MemoryPressureBanner", () => ({ MemoryPressureBanner: () => null }));
vi.mock("@/components/LanguageSwitcher", () => ({ LanguageSwitcher: () => null }));
vi.mock("@/components/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }));
vi.mock("@nous-research/ui/hooks/use-below-breakpoint", () => ({ useBelowBreakpoint: () => false }));

function renderAtOffice() {
  const router = createMemoryRouter([{ path: "*", element: createElement(App) }], {
    initialEntries: ["/qubit-office"],
  });
  return renderToStaticMarkup(createElement(RouterProvider, { router }));
}

function renderAt(path: string) {
  const router = createMemoryRouter([{ path: "*", element: createElement(App) }], {
    initialEntries: [path],
  });
  return renderToStaticMarkup(createElement(RouterProvider, { router }));
}

describe("App /qubit-office route", () => {
  it("routes the built-in path to the qubit-office plugin exactly once", () => {
    const html = renderAtOffice();
    expect(html).toContain('data-plugin="qubit-office"');
    expect(html.match(/data-plugin="qubit-office"/g)).toHaveLength(1);
    expect(html.match(/href="\/qubit-office"/g)).toHaveLength(1);
    expect(html).not.toContain("SessionsPage");
  });
});

describe("App /qubit-hud route", () => {
  it("exposes exactly one sidebar link to the HUD with its resolved label", () => {
    const html = renderAt("/qubit-hud");
    // The reachability claim: one clickable row for the path in the rail.
    expect(html.match(/href="\/qubit-hud"/g)).toHaveLength(1);
    // The label resolves through ROUTE_TITLES to "Qubit HUD", not "Qubit-hud".
    expect(html).toContain("Qubit HUD");
    expect(html).not.toContain("Qubit-hud");
  });

  it("mounts the qubit-hud plugin for the path", () => {
    // buildRoutes()'s hidden-tab pass is what keeps a `"hidden": true` plugin
    // routable at all; without it /qubit-hud would fall to the catch-all.
    mockManifests.push({
      name: "qubit-hud",
      label: "Qubit HUD",
      icon: "Gauge",
      tab: { path: "/qubit-hud", hidden: true, position: "end" },
    });
    try {
      const html = renderAt("/qubit-hud");
      expect(html).toContain('data-plugin="qubit-hud"');
      // A hidden tab still produces no second sidebar row — the builtin entry
      // is the only one, so there is no duplicate key.
      expect(html.match(/href="\/qubit-hud"/g)).toHaveLength(1);
    } finally {
      mockManifests.length = 0;
    }
  });
});
