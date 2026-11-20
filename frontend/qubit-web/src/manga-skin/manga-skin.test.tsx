// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MangaSkinGate } from "./MangaSkin";
import {
  MANGA_EXCLUDED_PATHS,
  MANGA_SKIN_KEY,
  bootstrapMangaSkin,
  mangaRouteEligible,
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
      <button type="button" onClick={() => navigate("/qubit-workbench")}>
        workbench
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
});

describe("manga route eligibility", () => {
  it("excludes every protected surface and its descendants", () => {
    for (const path of MANGA_EXCLUDED_PATHS) {
      expect(mangaRouteEligible(path)).toBe(false);
      expect(mangaRouteEligible(`${path}/child`)).toBe(false);
      expect(mangaRouteEligible(`${path}-extra`)).toBe(true);
    }
  });

  it("skins the /chat host shell while leaving the terminal as its own surface", () => {
    expect(mangaRouteEligible("/chat")).toBe(true);
    expect(mangaRouteEligible("/chat/child")).toBe(true);
  });

  it("allows ordinary dashboard routes", () => {
    for (const path of [
      "/",
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

    clickText("workbench");
    expect(skinOn()).toBe(false);

    clickText("office");
    expect(skinOn()).toBe(true);

    clickText("sessions");
    expect(skinOn()).toBe(true);
  });

  it("does not reload the asset manifests while navigating inside Manga", () => {
    const fetch = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetch);
    try {
      setMangaEnabled(true);
      mount("/models");
      expect(fetch).toHaveBeenCalledTimes(2);
      clickText("sessions");
      expect(fetch).toHaveBeenCalledTimes(2);
      clickText("workbench");
      expect(skinOn()).toBe(false);
      clickText("sessions");
      expect(fetch).toHaveBeenCalledTimes(4);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("stays off when the theme/preference is disabled", () => {
    mount("/sessions");
    setMangaEnabled(false);
    act(() => {});
    expect(skinOn()).toBe(false);
  });

  it("renders no separate skin switch", () => {
    setMangaEnabled(true);
    mount("/sessions");
    expect(document.querySelector(".qm-skin-switch")).toBeNull();
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
    expect(skinOn()).toBe(true);

    bootstrapMangaSkin("/qubit-workbench");
    expect(skinOn()).toBe(false);
  });
});
