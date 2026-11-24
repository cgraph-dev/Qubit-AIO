// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider, useTheme } from "./context";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getThemes: vi.fn(),
    getFontPref: vi.fn(),
    setTheme: vi.fn().mockResolvedValue(undefined),
    setFontPref: vi.fn().mockResolvedValue(undefined),
  },
}));

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-qubit-manga");
});

describe("appearance preferences", () => {
  it("keeps newer local choices when the initial server responses arrive late", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const themes = pending<Awaited<ReturnType<typeof api.getThemes>>>();
    const font = pending<Awaited<ReturnType<typeof api.getFontPref>>>();
    vi.mocked(api.getThemes).mockReturnValue(themes.promise);
    vi.mocked(api.getFontPref).mockReturnValue(font.promise);

    let preferences!: ReturnType<typeof useTheme>;
    function ReadPreferences() {
      preferences = useTheme();
      return null;
    }
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    try {
      await act(async () => root.render(<ThemeProvider><ReadPreferences /></ThemeProvider>));
      await act(async () => {
        preferences.setTheme("q6");
        preferences.setFont("system-serif");
      });
      await act(async () => {
        themes.resolve({
          themes: [{ name: "q4", label: "Cyber Ghost", description: "" }],
          active: "q4",
        });
        font.resolve({ font: "inter" });
      });
      expect(preferences.themeName).toBe("q6");
      expect(preferences.fontId).toBe("system-serif");
      expect(preferences.availableThemes[0].label).toBe("Cyber Ghost");
      expect(document.documentElement.style.getPropertyValue("--theme-font-display")).toContain("Georgia");
      expect(document.documentElement.style.getPropertyValue("--theme-font-mono")).toContain("JetBrains Mono");
      await act(async () => preferences.setFont("theme"));
      expect(document.documentElement.style.getPropertyValue("--theme-font-display")).toContain("Rajdhani");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
