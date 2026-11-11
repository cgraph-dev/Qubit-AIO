import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOADING_CONFIG, backgroundImageStyle, readInjectedLoadingConfig,
  resolveLoadingConfig, safeBackground,
} from "./config";

describe("background safety", () => {
  it("accepts art the owner can actually ship", () => {
    for (const value of [
      "https://cdn.example.com/office/loading.webp",
      "http://127.0.0.1:8890/media/office.png",
      "/media/office-loading.webp",
      "./media/office-loading.webp",
      "../assets/loading.png",
      "media/office-loading.webp",
      "media/office-loading.webp?v=3",
      "data:image/png;base64,iVBORw0KGgo=",
    ]) {
      expect(safeBackground(value)).toBe(value);
    }
  });

  it("rejects anything that could break out of the CSS value", () => {
    for (const value of [
      "", "   ", "javascript:alert(1)", "JavaScript:alert(1)",
      "vbscript:msgbox(1)", "file:///etc/passwd",
      "https://x.test/a\") ; background: red", "https://x.test/a b",
      "https://x.test/a\\b", "url(https://x.test/a)",
      `https://x.test/${"a".repeat(2100)}`,
    ]) {
      expect(safeBackground(value)).toBe("");
    }
    expect(safeBackground(42)).toBe("");
  });
});

describe("resolveLoadingConfig", () => {
  it("has a safe usable default", () => {
    const config = resolveLoadingConfig({}, {} as Window);
    expect(config.background).toBe("");
    expect(config.backgroundDim).toBeGreaterThan(0);
    expect(config.brand).toBe("Qubit Office");
    expect(config.effects).toBe(true);
    expect(config.location).toBeNull();
    expect(config.weather).toBeNull();
    expect(config.tips.length).toBeGreaterThan(0);
  });

  it("clamps the plan budgets instead of trusting a host value", () => {
    const config = resolveLoadingConfig({
      weatherTimeoutMs: 99_000,
      weatherCacheTtlMs: 1,
      hysteresisMs: -5,
      backgroundDim: 2,
    }, {} as Window);
    expect(config.weatherTimeoutMs).toBe(2000);
    expect(config.weatherCacheTtlMs).toBe(10 * 60 * 1000);
    expect(config.hysteresisMs).toBe(0);
    expect(config.backgroundDim).toBe(0.95);

    const low = resolveLoadingConfig({ weatherTimeoutMs: 1, weatherCacheTtlMs: 10 ** 9 }, {} as Window);
    expect(low.weatherTimeoutMs).toBe(500);
    expect(low.weatherCacheTtlMs).toBe(30 * 60 * 1000);
  });

  it("validates a configured location and ignores an impossible one", () => {
    expect(resolveLoadingConfig({
      location: { latitude: 44.4, longitude: 26.1, name: "Bucharest" },
    }, {} as Window).location).toMatchObject({ latitude: 44.4, longitude: 26.1 });
    expect(resolveLoadingConfig({
      location: { latitude: 999, longitude: 26.1 } as never,
    }, {} as Window).location).toBeNull();
  });

  it("normalizes an injected weather snapshot", () => {
    const config = resolveLoadingConfig({
      weather: { condition: "rain", temperatureC: 12, isDay: true },
    }, {} as Window);
    expect(config.weather?.condition).toBe("rain");
    expect(config.weather?.source).toBe("injected");
  });

  it("reads both documented host globals, and an explicit prop wins", () => {
    const win = {
      __QUBIT_OFFICE_LOADING__: { brand: "Direct Brand", background: "/a.png" },
    } as unknown as Window;
    expect(readInjectedLoadingConfig(win)?.brand).toBe("Direct Brand");
    expect(resolveLoadingConfig({}, win).brand).toBe("Direct Brand");

    const nested = {
      __QUBIT_OFFICE_CONFIG__: { office: { loading: { brand: "Nested Brand" } } },
    } as unknown as Window;
    expect(resolveLoadingConfig({}, nested).brand).toBe("Nested Brand");

    expect(resolveLoadingConfig({ brand: "Prop Brand" }, win).brand).toBe("Prop Brand");
  });

  it("falls back to the placeholder when the owner art is unsafe", () => {
    expect(resolveLoadingConfig({ background: "javascript:alert(1)" }, {} as Window).background).toBe("");
    expect(backgroundImageStyle(resolveLoadingConfig({ background: "/media/a.webp" }, {} as Window)))
      .toEqual({ backgroundImage: 'url("/media/a.webp")' });
    expect(backgroundImageStyle(resolveLoadingConfig({}, {} as Window))).toBeNull();
  });

  it("keeps the documented default timeout/cache range", () => {
    expect(DEFAULT_LOADING_CONFIG.weatherTimeoutMs).toBeGreaterThanOrEqual(1000);
    expect(DEFAULT_LOADING_CONFIG.weatherTimeoutMs).toBeLessThanOrEqual(2000);
    expect(DEFAULT_LOADING_CONFIG.weatherCacheTtlMs).toBeGreaterThanOrEqual(10 * 60 * 1000);
    expect(DEFAULT_LOADING_CONFIG.weatherCacheTtlMs).toBeLessThanOrEqual(30 * 60 * 1000);
  });
});
