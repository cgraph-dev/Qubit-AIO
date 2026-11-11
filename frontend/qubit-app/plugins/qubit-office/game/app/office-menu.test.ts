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

describe("office menu fallback (upstream DOM changes)", () => {
  const noRaf = () => {
    const real = window.requestAnimationFrame;
    (window as unknown as { requestAnimationFrame?: unknown })
      .requestAnimationFrame = undefined;
    return () => {
      (window as unknown as { requestAnimationFrame?: unknown })
        .requestAnimationFrame = real;
    };
  };

  it("shows a fallback after the bounded wait, then hands off when the group appears", async () => {
    vi.useFakeTimers();
    const restoreRaf = noRaf();
    try {
      document.body.innerHTML = "";
      const onFallback = vi.fn();
      const onAttach = vi.fn();
      const menu = installOfficeMenuButtons(window, {
        onToggleAgents: () => {},
        onToggleHud: () => {},
        fallbackAfterMs: 1500,
        onFallback,
        onAttach,
      });
      expect(menu?.attached()).toBe(false);
      expect(onFallback).not.toHaveBeenCalledWith(true);

      vi.advanceTimersByTime(1500);
      expect(onFallback).toHaveBeenCalledWith(true);

      // The group appears later (an app mount after the load event).
      const group = makeMenu();
      await Promise.resolve();
      vi.advanceTimersByTime(10);
      await Promise.resolve();

      expect(onFallback).toHaveBeenLastCalledWith(false);
      expect(onAttach).toHaveBeenCalledTimes(1);
      expect(menu?.attached()).toBe(true);
      // Never both: one injected pair, and the shell was told to hide its own.
      expect(group.querySelectorAll('[data-testid="office-menu-agents"]')).toHaveLength(1);
      expect(group.querySelectorAll('[data-testid="office-menu-hud"]')).toHaveLength(1);
      menu?.dispose();
    } finally {
      restoreRaf();
      vi.useRealTimers();
    }
  });

  it("shows no fallback when the group is already there", () => {
    vi.useFakeTimers();
    const restoreRaf = noRaf();
    try {
      makeMenu();
      const onFallback = vi.fn();
      const menu = installOfficeMenuButtons(window, {
        onToggleAgents: () => {},
        onToggleHud: () => {},
        fallbackAfterMs: 1500,
        onFallback,
      });
      expect(menu?.attached()).toBe(true);
      vi.advanceTimersByTime(1500);
      expect(onFallback).not.toHaveBeenCalledWith(true);
      menu?.dispose();
    } finally {
      restoreRaf();
      vi.useRealTimers();
    }
  });

  it("dispose clears a shown fallback", () => {
    vi.useFakeTimers();
    const restoreRaf = noRaf();
    try {
      document.body.innerHTML = "";
      const onFallback = vi.fn();
      const menu = installOfficeMenuButtons(window, {
        onToggleAgents: () => {},
        onToggleHud: () => {},
        fallbackAfterMs: 1500,
        onFallback,
      });
      vi.advanceTimersByTime(1500);
      expect(onFallback).toHaveBeenCalledWith(true);
      menu?.dispose();
      expect(onFallback).toHaveBeenLastCalledWith(false);
    } finally {
      restoreRaf();
      vi.useRealTimers();
    }
  });
});

describe("approvals icon and pending badge", () => {
  it("adds a third icon before Settings and forwards its click", () => {
    const group = makeMenu();
    const onToggleApprovals = vi.fn();
    const menu = installOfficeMenuButtons(window, {
      onToggleAgents: () => {},
      onToggleHud: () => {},
      onToggleApprovals,
    });
    const approvals = group.querySelector(
      '[data-testid="office-menu-approvals"]') as HTMLButtonElement;
    expect(approvals).not.toBeNull();
    // Order is unchanged: the new icon lands after the other two, before the
    // app's own utilities.
    expect(group.children[4].getAttribute("data-testid"))
      .toBe("office-menu-approvals");
    expect(group.children[5].getAttribute("title")).toBe("Settings");
    expect(approvals.querySelector("svg")?.getAttribute("shape-rendering"))
      .toBe("crispEdges");
    approvals.click();
    expect(onToggleApprovals).toHaveBeenCalledTimes(1);
    menu?.dispose();
  });

  it("paints the live pending count and hides the badge at zero", () => {
    const group = makeMenu();
    const menu = installOfficeMenuButtons(window, {
      onToggleAgents: () => {},
      onToggleHud: () => {},
      onToggleApprovals: () => {},
    });
    const approvals = group.querySelector(
      '[data-testid="office-menu-approvals"]') as HTMLButtonElement;
    const badge = approvals.querySelector(
      '[data-testid="office-menu-approvals-badge"]') as HTMLSpanElement;
    expect(badge).not.toBeNull();
    expect(badge.hidden).toBe(true);

    menu?.update({ agents: false, hud: false, approvals: false,
                   pendingApprovals: 3 });
    expect(approvals.getAttribute("aria-pressed")).toBe("false");
    expect(approvals.getAttribute("aria-label")).toBe("Approvals (3 pending)");
    expect(approvals.getAttribute("data-pending")).toBe("3");
    expect(badge.textContent).toBe("3");
    expect(badge.hidden).toBe(false);

    menu?.update({ agents: false, hud: false, approvals: true,
                   pendingApprovals: 0 });
    expect(approvals.getAttribute("aria-pressed")).toBe("true");
    expect(approvals.getAttribute("aria-label")).toBe("Approvals");
    expect(approvals.getAttribute("data-pending")).toBe("0");
    expect(badge.textContent).toBe("");
    expect(badge.hidden).toBe(true);
    menu?.dispose();
  });
});
