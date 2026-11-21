// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const setTheme = vi.fn();
const setFont = vi.fn();
const setLocale = vi.fn();

vi.mock("@/i18n/context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/i18n/context")>();
  return {
    ...actual,
    useI18n: () => ({
      locale: "en",
      setLocale,
      t: {
        language: { switchTo: "Language" },
        common: { close: "Close" },
        theme: {
          title: "Theme",
          switchTheme: "Switch theme",
          fontTitle: "Font",
          fontDefault: "Theme default",
          fontDefaultHint: "Use the active theme's font",
          fontSans: "Sans",
          fontSerif: "Serif",
          fontMono: "Mono",
        },
      },
    }),
  };
});

vi.mock("@/themes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/themes")>();
  return {
    ...actual,
    useTheme: () => ({
      themeName: "q2",
      availableThemes: [
        { name: "q2", label: "Midnight", description: "Midnight palette" },
        { name: "q4", label: "Manga", description: "Ink and gold" },
      ],
      setTheme,
      fontId: actual.THEME_DEFAULT_FONT_ID,
      fontChoices: [],
      setFont,
    }),
  };
});

import { ThemeSwitcher } from "./ThemeSwitcher";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { setMangaEnabled } from "@/manga-skin/manga-store";

let root: Root | null = null;
let host: HTMLDivElement | null = null;

function mount(node: ReactNode) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(node));
}

function popover() {
  return document.body.querySelector<HTMLElement>(".qm-select-popover");
}

beforeEach(() => {
  setMangaEnabled(true);
  document.documentElement.dataset.qubitManga = "on";
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  host?.remove();
  host = null;
  document.querySelector(".qm-select-popover")?.remove();
  setMangaEnabled(false);
  delete document.documentElement.dataset.qubitManga;
  setTheme.mockReset();
  setFont.mockReset();
  setLocale.mockReset();
});

describe("Manga utility pickers", () => {
  it("opens the shared panel and filters themes", () => {
    mount(<ThemeSwitcher />);
    act(() => host!.querySelector<HTMLButtonElement>(".qm-theme-trigger")!.click());
    expect(popover()?.classList.contains("qm-picker-panel")).toBe(true);
    const filter = popover()!.querySelector<HTMLInputElement>(".qm-select-filter")!;
    expect(filter.getAttribute("aria-label")).toBe("Filter Theme");

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(filter, "Manga");
      filter.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const rows = popover()!.querySelectorAll('[role="option"]');
    expect(Array.from(rows).map((row) => row.textContent)).toEqual([
      expect.stringContaining("Manga"),
    ]);
    act(() => (rows[0] as HTMLButtonElement).click());
    expect(setTheme).toHaveBeenCalledWith("q4");
    expect(popover()).toBeNull();
  });

  it("filters languages, selects one and restores trigger focus on Escape", () => {
    mount(<LanguageSwitcher />);
    const trigger = host!.querySelector<HTMLButtonElement>(".qm-language-trigger")!;
    act(() => trigger.click());
    const filter = popover()!.querySelector<HTMLInputElement>(".qm-select-filter")!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(filter, "Français");
      filter.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const rows = popover()!.querySelectorAll('[role="option"]');
    expect(rows).toHaveLength(1);
    act(() => (rows[0] as HTMLButtonElement).click());
    expect(setLocale).toHaveBeenCalledWith("fr");

    act(() => trigger.click());
    act(() => popover()!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(popover()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
