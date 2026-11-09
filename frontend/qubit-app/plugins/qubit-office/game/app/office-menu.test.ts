// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AGENTS_ICON,
  HUD_ICON,
  MENU_BUTTONS,
  installOfficeMenuButtons,
} from "./office-menu";

const makeMenu = () => {
  document.body.innerHTML = "";
  const group = document.createElement("div");
  group.className = "ui-btn-group";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Panels");
  for (const title of ["My day", "Activity feed", "Settings", "Help"]) {
    const b = document.createElement("button");
    b.className = "ui-icon-btn";
    b.type = "button";
    b.title = title;
    group.appendChild(b);
  }
  document.body.appendChild(group);
  return group;
};

const settle = () => new Promise((resolve) => setTimeout(resolve, 25));

afterEach(() => {
  document.body.innerHTML = "";
});

describe("office menu integration", () => {
  it("adds the two icons before Settings without moving the existing buttons", () => {
    const group = makeMenu();
    const before = Array.from(group.children).map((n) => (n as HTMLElement).title);
    const onToggleAgents = vi.fn();
    const onToggleHud = vi.fn();

    const menu = installOfficeMenuButtons(window, { onToggleAgents, onToggleHud });

    const titles = Array.from(group.children).map((n) => (n as HTMLElement).title);
    // The existing order is preserved exactly, and the two icons land between
    // the tools and the utilities.
    expect(titles.slice(0, 2)).toEqual(before.slice(0, 2));
    expect(titles.slice(-2)).toEqual(["Settings", "Help"]);
    expect(group.querySelector('[data-testid="office-menu-agents"]')).not.toBeNull();
    expect(group.querySelector('[data-testid="office-menu-hud"]')).not.toBeNull();
    expect(group.children[2].getAttribute("data-testid")).toBe("office-menu-agents");
    expect(group.children[3].getAttribute("data-testid")).toBe("office-menu-hud");

    (group.querySelector('[data-testid="office-menu-agents"]') as HTMLButtonElement).click();
    (group.querySelector('[data-testid="office-menu-hud"]') as HTMLButtonElement).click();
    expect(onToggleAgents).toHaveBeenCalledTimes(1);
    expect(onToggleHud).toHaveBeenCalledTimes(1);
    menu?.dispose();
  });

  it("draws the traced icons in the app's own pixel-icon convention", () => {
    const group = makeMenu();
    const menu = installOfficeMenuButtons(window, {
      onToggleAgents: () => {},
      onToggleHud: () => {},
    });
    const svg = group.querySelector('[data-testid="office-menu-agents"] svg');
    expect(svg?.getAttribute("class")).toBe("ui-px-icon");
    expect(svg?.getAttribute("shape-rendering")).toBe("crispEdges");
    expect(svg?.getAttribute("viewBox")).toBe(`0 0 ${AGENTS_ICON.cols} ${AGENTS_ICON.rows}`);
    expect(svg?.querySelector("path")?.getAttribute("fill")).toBe("currentColor");
    const hud = group.querySelector('[data-testid="office-menu-hud"] svg');
    expect(hud?.getAttribute("viewBox")).toBe(`0 0 ${HUD_ICON.cols} ${HUD_ICON.rows}`);
    menu?.dispose();
  });

  it("mirrors the dock state onto aria-pressed, which is the app's active style", () => {
    const group = makeMenu();
    const menu = installOfficeMenuButtons(window, {
      onToggleAgents: () => {},
      onToggleHud: () => {},
    });
    const agents = group.querySelector('[data-testid="office-menu-agents"]') as HTMLElement;
    const hud = group.querySelector('[data-testid="office-menu-hud"]') as HTMLElement;
    expect(agents.getAttribute("aria-pressed")).toBe("false");

    menu?.update({ agents: true, hud: false });
    expect(agents.getAttribute("aria-pressed")).toBe("true");
    expect(hud.getAttribute("aria-pressed")).toBe("false");

    menu?.update({ agents: false, hud: true });
    expect(agents.getAttribute("aria-pressed")).toBe("false");
    expect(hud.getAttribute("aria-pressed")).toBe("true");
    menu?.dispose();
  });

  it("restores the icons when a re-render drops them, and disposes cleanly", async () => {
    const group = makeMenu();
    const menu = installOfficeMenuButtons(window, {
      onToggleAgents: () => {},
      onToggleHud: () => {},
    });
    group.querySelector('[data-testid="office-menu-agents"]')!.remove();
    await settle();
    expect(group.querySelector('[data-testid="office-menu-agents"]')).not.toBeNull();

    // React replacing the whole group is recovered too.
    group.remove();
    const fresh = makeMenu();
    await settle();
    expect(fresh.querySelector('[data-testid="office-menu-hud"]')).not.toBeNull();

    menu?.dispose();
    expect(document.querySelector('[data-testid="office-menu-hud"]')).toBeNull();
  });

  it("keeps every supplied icon non-empty", () => {
    for (const spec of MENU_BUTTONS) {
      expect(spec.icon.d).toMatch(/^M/);
      expect(spec.icon.cols).toBeGreaterThan(4);
      expect(spec.icon.rows).toBeGreaterThan(4);
    }
  });
});
