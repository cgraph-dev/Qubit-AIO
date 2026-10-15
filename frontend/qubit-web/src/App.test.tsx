import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { renderToStaticMarkup } from "react-dom/server";
import App from "./App";

vi.mock("@/plugins", () => ({
  PluginPage: ({ name }: { name: string }) =>
    createElement("div", { "data-testid": "plugin-page", "data-plugin": name }),
  PluginSlot: () => null,
  usePlugins: () => ({ manifests: [], loading: false }),
}));
vi.mock("@/i18n", () => ({ useI18n: () => ({ t: { app: { openNavigation: "Open navigation", navigation: "Navigation", brand: "Qubit", closeNavigation: "Close navigation", pluginNavSection: "Plugins", nav: { chat: "Chat", sessions: "Sessions", analytics: "Analytics", models: "Models", cron: "Cron", skills: "Skills", profiles: "Profiles", config: "Config", keys: "Keys", documentation: "Documentation" } }, common: { expand: "Expand", collapse: "Collapse" }, status: { gateway: "Gateway" }, theme: { switchTheme: "Switch theme" }, language: { switchTo: "Language" } } }) }));
vi.mock("@/themes", () => ({ useTheme: () => ({ theme: {} }) }));
vi.mock("@/hooks/useSidebarStatus", () => ({ useSidebarStatus: () => null }));
vi.mock("@/contexts/useSystemActions", () => ({ useSystemActions: () => ({}) }));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({}) }));
vi.mock("@/contexts/ProfileProvider", () => ({ ProfileProvider: ({ children }: { children: any }) => children }));
vi.mock("@/contexts/PageHeaderProvider", () => ({ PageHeaderProvider: ({ children }: { children: any }) => children }));
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

describe("App /qubit-office route", () => {
  it("routes the built-in path to the qubit-office plugin exactly once", () => {
    const html = renderAtOffice();
    expect(html).toContain('data-plugin="qubit-office"');
    expect(html.match(/data-plugin="qubit-office"/g)).toHaveLength(1);
    expect(html.match(/href="\/qubit-office"/g)).toHaveLength(1);
    expect(html).not.toContain("SessionsPage");
  });
});
