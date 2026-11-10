// @vitest-environment jsdom
/*
 * Task 6 — the HOST side of the office theme.
 *
 * The office app document is themed by the server (`_serve_app_html`). The
 * Qubit shell AROUND the iframe is a different document, so it loads the same
 * validated payload and links the generated host stylesheet. These tests pin
 * the whole path: classic makes no request, an active theme makes exactly one
 * `no-store` request, the host gets `data-office-theme` + ONE link (with
 * `?v=`), applying is idempotent, dispose removes only what it installed, and
 * the `?theme=` override reaches both the loader and the iframe URL.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyHostTheme,
  loadOfficeTheme,
  officeSliceName,
  officeThemeClass,
  themeCss,
  type OfficeTheme,
} from "../../../qubit-app/plugins/qubit-office/game/app/office-theme";
import { officeAppSrc } from "../../../qubit-app/plugins/qubit-office/game/app/office-app";
import { installOfficeMenuButtons } from "../../../qubit-app/plugins/qubit-office/game/app/office-menu";

const activeTheme: OfficeTheme = {
  id: "demo",
  label: "Demo",
  version: "1.0.0",
  credit: "LimeZu — https://limezu.itch.io",
  license: "LimeZu licence",
  active: true,
  assetBase: "/plugins/qubit-office/themes/demo",
  atlas: "/plugins/qubit-office/themes/demo/portraits/atlas.png?v=1.0.0",
  index: "/plugins/qubit-office/themes/demo/portraits/index.json?v=1.0.0",
  bodies: { atlas: null, index: null, size: 32, frame: { w: 32, h: 64 } },
  tokens: { "color-surface": "#0d0d1a" },
  slices: {},
};

const classicTheme: OfficeTheme = {
  ...activeTheme,
  id: "classic",
  active: false,
  assetBase: "",
  atlas: null,
  index: null,
  bodies: { atlas: null, index: null, size: null, frame: null },
  tokens: {},
};

function fakeFetch(payload: OfficeTheme | null = activeTheme) {
  const calls: Array<{ url: string; init?: Record<string, unknown> }> = [];
  const impl = vi.fn(async (url: string, init?: Record<string, unknown>) => {
    calls.push({ url, init });
    return { ok: true, status: 200, json: async () => payload };
  });
  return { impl, calls };
}

function resetHost() {
  delete (window as { __QUBIT_OFFICE_THEME__?: unknown })
    .__QUBIT_OFFICE_THEME__;
  delete (window as { __QUBIT_SESSION_TOKEN__?: string })
    .__QUBIT_SESSION_TOKEN__;
  delete (window as { __QUBIT_BASE_PATH__?: string }).__QUBIT_BASE_PATH__;
  document.documentElement.removeAttribute("data-office-theme");
  document.head.innerHTML = "";
  document.body.innerHTML = "";
}

afterEach(() => {
  resetHost();
  vi.restoreAllMocks();
});

describe("loadOfficeTheme", () => {
  it("uses the server-injected global and makes no request", async () => {
    (window as { __QUBIT_OFFICE_THEME__?: unknown }).__QUBIT_OFFICE_THEME__ =
      activeTheme;
    const { impl } = fakeFetch();
    await expect(loadOfficeTheme(impl as never, "", undefined)).resolves
      .toMatchObject({ id: "demo", active: true });
    expect(impl).not.toHaveBeenCalled();
  });

  it("a classic global applies nothing and makes no request", async () => {
    (window as { __QUBIT_OFFICE_THEME__?: unknown }).__QUBIT_OFFICE_THEME__ =
      classicTheme;
    const { impl } = fakeFetch();
    expect(await loadOfficeTheme(impl as never, "", undefined))
      .toMatchObject({ id: "classic", active: false });
    expect(impl).not.toHaveBeenCalled();
  });

  it("falls back to one authenticated fetch when the global is absent", async () => {
    (window as { __QUBIT_SESSION_TOKEN__?: string }).__QUBIT_SESSION_TOKEN__ =
      "tok-123";
    const { impl, calls } = fakeFetch();
    const theme = await loadOfficeTheme(impl as never, "", "demo");
    expect(theme?.id).toBe("demo");
    expect(impl).toHaveBeenCalledTimes(1);
    expect(calls[0].url).toBe("/agent/api/office/theme?theme=demo");
    expect(calls[0].init).toMatchObject({ cache: "no-store" });
    expect((calls[0].init?.headers as Record<string, string>)
      ["X-Qubit-Session-Token"]).toBe("tok-123");
  });

  it("fetches without a theme query when none was requested", async () => {
    const { impl, calls } = fakeFetch();
    await loadOfficeTheme(impl as never, "", undefined);
    expect(calls[0].url).toBe("/agent/api/office/theme");
  });

  it("encodes the requested id and honours a dashboard base", async () => {
    const { impl, calls } = fakeFetch();
    await loadOfficeTheme(impl as never, "/dash/", "demo pack");
    expect(calls[0].url).toBe("/dash/agent/api/office/theme?theme=demo%20pack");
  });

  it("an invalid override resolves the server's classic answer", async () => {
    const { impl } = fakeFetch(classicTheme);
    expect(await loadOfficeTheme(impl as never, "", "nope"))
      .toMatchObject({ id: "classic", active: false });
  });

  it("an invalid global shape falls through to the fetch", async () => {
    (window as { __QUBIT_OFFICE_THEME__?: unknown }).__QUBIT_OFFICE_THEME__ =
      { id: "", active: "yes" };
    const { impl } = fakeFetch();
    expect(await loadOfficeTheme(impl as never, "", "demo")).toMatchObject({
      id: "demo",
    });
    expect(impl).toHaveBeenCalledTimes(1);
  });

  it("a failed request resolves null", async () => {
    const impl = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    expect(await loadOfficeTheme(impl as never, "", "demo")).toBeNull();
  });
});

describe("applyHostTheme", () => {
  it("adds data-office-theme and exactly one link with ?v=", () => {
    const dispose = applyHostTheme(document, activeTheme);
    expect(document.documentElement.getAttribute("data-office-theme")).toBe("demo");
    const links = document.querySelectorAll("link[data-office-theme]");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("rel")).toBe("stylesheet");
    const href = links[0].getAttribute("href") || "";
    expect(href).toBe("/plugins/qubit-office/themes/demo/theme.css?v=1.0.0");
    expect(href).toContain("?v=1.0.0");
    dispose();
  });

  it("is idempotent: applying twice leaves one link", () => {
    const first = applyHostTheme(document, activeTheme);
    const second = applyHostTheme(document, activeTheme);
    expect(document.querySelectorAll("link[data-office-theme]")).toHaveLength(1);
    expect(document.documentElement.getAttribute("data-office-theme")).toBe("demo");
    second();
    // The first disposer must not tear down the newer application.
    first();
    expect(document.querySelectorAll("link[data-office-theme]")).toHaveLength(0);
    expect(document.documentElement.getAttribute("data-office-theme")).toBeNull();
  });

  it("classic adds no class and no link", () => {
    applyHostTheme(document, classicTheme);
    applyHostTheme(document, null);
    expect(document.documentElement.getAttribute("data-office-theme")).toBeNull();
    expect(document.querySelectorAll("link[data-office-theme]")).toHaveLength(0);
  });

  it("dispose removes the link and the attribute", () => {
    const dispose = applyHostTheme(document, activeTheme);
    dispose();
    expect(document.querySelectorAll("link[data-office-theme]")).toHaveLength(0);
    expect(document.documentElement.getAttribute("data-office-theme")).toBeNull();
  });

  it("themeCss is empty for classic and carries the version for active", () => {
    expect(themeCss(classicTheme)).toBe("");
    expect(themeCss(activeTheme)).toBe(
      "/plugins/qubit-office/themes/demo/theme.css?v=1.0.0");
  });

  it("themeCss honours the dashboard base path (subpath deployments)", () => {
    (window as { __QUBIT_BASE_PATH__?: string }).__QUBIT_BASE_PATH__ = "/dash/";
    expect(themeCss(activeTheme)).toBe(
      "/dash/plugins/qubit-office/themes/demo/theme.css?v=1.0.0");
  });
});

describe("host shell + menu hooks", () => {
  it("officeThemeClass is the stable shell class, empty for classic", () => {
    expect(officeThemeClass(activeTheme)).toBe("office-theme-demo");
    expect(officeThemeClass(classicTheme)).toBe("");
    expect(officeThemeClass(null)).toBe("");
  });

  it("maps a declared 9-slice role and ignores the rest", () => {
    const sliced: OfficeTheme = {
      ...activeTheme,
      slices: {
        dock: {
          src: "ui/dock.png",
          slice: [8, 8, 8, 8],
          width: "8px",
          repeat: "stretch",
        },
      },
    };
    expect(officeSliceName(sliced, "dock")).toBe("dock");
    expect(officeSliceName(sliced, "card")).toBeUndefined();
    expect(officeSliceName(sliced, "button")).toBeUndefined();
    expect(officeSliceName(classicTheme, "dock")).toBeUndefined();
    expect(officeSliceName(null, "dock")).toBeUndefined();
  });

  it("?theme= is appended to the iframe URL and classic is not", () => {
    expect(officeAppSrc({ base: "", theme: "demo" }))
      .toBe("/plugin-apps/qubit-office/habblaud/?theme=demo");
    expect(officeAppSrc({ base: "", theme: "classic" }))
      .toBe("/plugin-apps/qubit-office/habblaud/");
    expect(officeAppSrc({ base: "", mock: true, theme: "demo" }))
      .toBe("/plugin-apps/qubit-office/habblaud/?mock=1&theme=demo");
  });

  it("the injected menu buttons carry stable theme classes", () => {
    const group = document.createElement("div");
    group.className = "ui-btn-group";
    group.setAttribute("aria-label", "Panels");
    const settings = document.createElement("button");
    settings.className = "ui-icon-btn";
    settings.title = "Settings";
    group.appendChild(settings);
    document.body.appendChild(group);

    const menu = installOfficeMenuButtons(window, {
      onToggleAgents: () => {},
      onToggleHud: () => {},
    });
    const agents = group.querySelector('[data-testid="office-menu-agents"]');
    const hud = group.querySelector('[data-testid="office-menu-hud"]');
    expect(agents?.className).toContain("office-theme-menu-btn");
    expect(agents?.className).toContain("office-theme-menu-agents");
    expect(hud?.className).toContain("office-theme-menu-btn");
    expect(hud?.className).toContain("office-theme-menu-hud");
    menu?.dispose();
  });
});
