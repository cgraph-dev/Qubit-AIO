// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/sakuga/ui", () => ({
  emitFx: vi.fn(() => Promise.resolve()),
}));

import { MangaSkinGate } from "./MangaSkin";
import {
  MANGA_EXCLUDED_PATHS,
  MANGA_SKIN_KEY,
  bootstrapMangaSkin,
  mangaRouteEligible,
  readMangaPreference,
  setMangaEnabled,
} from "./manga-store";

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function mount(initial: string) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      <MemoryRouter initialEntries={[initial]}>
        <MangaSkinGate />
        <RouteButtons />
      </MemoryRouter>,
    );
  });
}

function RouteButtons() {
  const navigate = useNavigate();
  return (
    <div>
      <button type="button" onClick={() => navigate("/sessions")}>
        sessions
      </button>
      <button type="button" onClick={() => navigate("/qubit-office")}>
        office
      </button>
    </div>
  );
}

function clickText(label: string) {
  const button = [...document.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!button) throw new Error(`button not found: ${label}`);
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function skinOn(): boolean {
  return document.documentElement.dataset.qubitManga === "on";
}

beforeEach(() => {
  setMangaEnabled(false);
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-qubit-manga");
  document.body.innerHTML = "";
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  container?.remove();
  container = null;
  document
    .querySelectorAll(".qagent-sidebar__utility")
    .forEach((node) => node.remove());
});

describe("manga route eligibility", () => {
  it("excludes every protected surface and its descendants", () => {
    for (const path of MANGA_EXCLUDED_PATHS) {
      expect(mangaRouteEligible(path)).toBe(false);
      expect(mangaRouteEligible(`${path}/child`)).toBe(false);
      expect(mangaRouteEligible(`${path}-extra`)).toBe(true);
    }
  });

  it("allows ordinary dashboard routes", () => {
    for (const path of [
      "/",
      "/chat",
      "/qubit-chat",
      "/sessions",
      "/analytics",
      "/models",
      "/config",
      "/files",
      "/qubit-tv",
    ]) {
      expect(mangaRouteEligible(path)).toBe(true);
    }
  });
});

describe("MangaSkinGate", () => {
  it("marks the document only on eligible routes", () => {
    setMangaEnabled(true);
    mount("/sessions");
    expect(skinOn()).toBe(true);

    clickText("office");
    expect(skinOn()).toBe(false);

    clickText("sessions");
    expect(skinOn()).toBe(true);
  });

  it("stays off when the preference is disabled", () => {
    mount("/sessions");
    setMangaEnabled(false);
    act(() => {});
    expect(skinOn()).toBe(false);
  });
});

describe("MangaSkinToggle", () => {
  function installHost() {
    const sidebar = document.createElement("aside");
    sidebar.id = "app-sidebar";
    const host = document.createElement("div");
    host.className = "qagent-sidebar__utility";
    sidebar.append(host);
    document.body.append(sidebar);
    return host;
  }

  it("renders beside the theme controls with an accessible state", () => {
    installHost();
    mount("/sessions");
    const toggle = document.querySelector<HTMLButtonElement>(".qm-skin-switch");
    expect(toggle).not.toBeNull();
    expect(toggle!.getAttribute("aria-pressed")).toBe("false");
    expect(toggle!.getAttribute("aria-label")).toMatch(/enable/i);
    expect(toggle!.textContent).toContain("Manga");
  });

  it("persists ON/OFF and restores the default appearance", () => {
    installHost();
    mount("/sessions");
    const toggle = () =>
      document.querySelector<HTMLButtonElement>(".qm-skin-switch")!;

    act(() => toggle().click());
    expect(readMangaPreference()).toBe(true);
    expect(window.localStorage.getItem(MANGA_SKIN_KEY)).toBe("true");
    expect(skinOn()).toBe(true);
    expect(toggle().getAttribute("aria-pressed")).toBe("true");

    act(() => toggle().click());
    expect(readMangaPreference()).toBe(false);
    expect(window.localStorage.getItem(MANGA_SKIN_KEY)).toBe("false");
    expect(skinOn()).toBe(false);
    expect(toggle().getAttribute("aria-pressed")).toBe("false");
  });

  it("is not offered on an excluded route", () => {
    installHost();
    mount("/qubit-office");
    expect(document.querySelector(".qm-skin-switch")).toBeNull();
    expect(skinOn()).toBe(false);
  });
});

describe("bootstrapMangaSkin", () => {
  it("applies a stored preference before React paints", () => {
    window.localStorage.setItem(MANGA_SKIN_KEY, "true");
    // Reset the in-memory cache so the read comes from storage.
    setMangaEnabled(false);
    window.localStorage.setItem(MANGA_SKIN_KEY, "true");

    bootstrapMangaSkin("/sessions");
    expect(skinOn()).toBe(true);

    bootstrapMangaSkin("/qubit-office");
    expect(skinOn()).toBe(false);
  });
});
