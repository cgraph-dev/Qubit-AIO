// @vitest-environment jsdom
/*
 * The loading screen's real React surface. The shell's own harness does not
 * render child components, so these tests mount the actual component with the
 * host's React (the same single copy the plugin receives) and a fake 2D
 * context/scheduler so every frame is deterministic.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createOfficeLoadingScreen, type OfficeLoadingScreenProps } from "./OfficeLoadingScreen";

declare global {
  // React's act() environment flag (not in the DOM lib).
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const putImageData = vi.fn();
let rafId = 0;
const pending = new Map<number, FrameRequestCallback>();
const raf = vi.fn((cb: FrameRequestCallback) => {
  const id = ++rafId;
  pending.set(id, cb);
  return id;
});
const caf = vi.fn((id: number) => { pending.delete(id); });

const Component = createOfficeLoadingScreen(
  React as never,
  { useState: React.useState, useEffect: React.useEffect, useRef: React.useRef } as never,
);

beforeAll(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: vi.fn(() => ({
      createImageData: (w: number, h: number) => ({
        data: new Uint8ClampedArray(w * h * 4),
      }),
      putImageData,
    })),
  });
});

beforeEach(() => {
  pending.clear();
  rafId = 0;
  putImageData.mockClear();
  raf.mockClear();
  caf.mockClear();
  vi.stubGlobal("requestAnimationFrame", raf);
  vi.stubGlobal("cancelAnimationFrame", caf);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function render(props: OfficeLoadingScreenProps) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(Component as never, props));
  });
  return { container, root };
}

const rainWeather = { condition: "rain", temperatureC: 12, isDay: true } as const;

describe("loading screen", () => {
  it("renders the brand, placeholder and an accessible announcement immediately", async () => {
    const { container, root } = await render({
      config: { weather: rainWeather, effects: false },
    });
    const screen = container.querySelector('[data-testid="office-loading"]');
    expect(screen).not.toBeNull();
    expect(screen?.getAttribute("data-phase")).toBe("loading");
    expect(screen?.getAttribute("aria-busy")).toBe("true");
    expect(container.textContent).toContain("Qubit Office");
    expect(container.textContent).toContain("Nobody knows what it will become.");
    expect(container.querySelector('[data-testid="office-loading-status"]')?.textContent)
      .toContain("Preparing the office");
    // Placeholder: no owner image is applied.
    const bg = container.querySelector(".office-loading__bg") as HTMLElement;
    expect(bg.style.backgroundImage).toBe("");
    // Weather is announced once, outside the animation frames.
    expect(container.querySelector('[data-testid="office-loading-announcer"]')?.textContent)
      .toContain("rain");
    const progress = container.querySelector('[data-testid="office-loading-progress"]');
    expect(progress?.getAttribute("role")).toBe("progressbar");
    expect(progress?.getAttribute("aria-valuenow")).toBeNull();
    root.unmount();
  });

  it("applies the owner background and a real progress value", async () => {
    const { container, root } = await render({
      progress: 0.42,
      config: { background: "/media/office-loading.webp", effects: false },
    });
    const bg = container.querySelector(".office-loading__bg") as HTMLElement;
    expect(bg.style.backgroundImage).toContain("office-loading.webp");
    const progress = container.querySelector('[data-testid="office-loading-progress"]');
    expect(progress?.getAttribute("aria-valuenow")).toBe("42");
    // The vendored progress bar reads its fraction from `value`; the visible
    // fill lives in the component's shadow tree, not a light-DOM span.
    expect(progress?.getAttribute("value")).toBe("0.42");
    root.unmount();
  });

  it("shows the failure as an alert and retries through the shell", async () => {
    const onRetry = vi.fn();
    const { container, root } = await render({
      phase: "error",
      error: "The office app failed to load. (HTTP 500)",
      onRetry,
      config: { effects: false },
    });
    const status = container.querySelector('[data-testid="office-loading-status"]');
    expect(status?.getAttribute("role")).toBe("alert");
    expect(status?.textContent).toContain("HTTP 500");
    const button = container.querySelector(
      '[data-testid="office-loading-retry-button"]') as HTMLButtonElement;
    expect(button.textContent).toBe("Retry");
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onRetry).toHaveBeenCalledTimes(1);
    root.unmount();
  });

  it("draws one still frame per layer under reduced motion and never loops", async () => {
    const { container, root } = await render({
      prefersReducedMotion: true,
      config: { weather: rainWeather, effects: true },
    });
    const screen = container.querySelector('[data-testid="office-loading"]');
    expect(screen?.getAttribute("data-reduced-motion")).toBe("1");
    expect(container.querySelector('[data-testid="office-loading-fx-back"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="office-loading-fx-front"]')).not.toBeNull();
    expect(putImageData).toHaveBeenCalledTimes(2);
    expect(pending.size).toBe(0);
    root.unmount();
  });

  it("animates both layers, then fades out on ready and releases every frame", async () => {
    // Fake the fade/tip timers only: requestAnimationFrame is our driven stub.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    const props: OfficeLoadingScreenProps = {
      config: { weather: rainWeather, effects: true },
    };
    const { container, root } = await render(props);
    expect(pending.size).toBe(2); // back + front surfaces

    await act(async () => {
      const callbacks = [...pending.values()];
      pending.clear();
      for (const cb of callbacks) cb(100);
    });
    expect(putImageData).toHaveBeenCalledTimes(2);
    expect(pending.size).toBe(2);

    await act(async () => {
      root.render(React.createElement(Component as never, { ...props, ready: true }));
    });
    const screen = container.querySelector('[data-testid="office-loading"]');
    expect(screen?.getAttribute("data-leaving")).toBe("1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(container.querySelector('[data-testid="office-loading"]')).toBeNull();
    expect(caf).toHaveBeenCalled();
    expect(pending.size).toBe(0);
    root.unmount();
  });

  it("names an offline state instead of spinning forever", async () => {
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    try {
      const { container, root } = await render({ config: { effects: false } });
      const screen = container.querySelector('[data-testid="office-loading"]');
      expect(screen?.getAttribute("data-offline")).toBe("1");
      expect(container.textContent).toContain("Offline");
      root.unmount();
    } finally {
      Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    }
  });

  it("never opens the network without a configured location", async () => {
    const weatherLoader = vi.fn();
    const { container, root } = await render({
      weatherLoader: weatherLoader as never,
      config: { effects: false },
    });
    expect(weatherLoader).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="office-loading-weather"]')?.textContent)
      .toContain("neutral");
    root.unmount();
  });

  it("uses an injected weather snapshot without a fetch", async () => {
    const weatherLoader = vi.fn();
    const { container, root } = await render({
      weatherLoader: weatherLoader as never,
      config: { weather: { condition: "snow", temperatureC: -3 }, effects: false },
    });
    expect(weatherLoader).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="office-loading-weather"]')?.textContent)
      .toContain("snow");
    root.unmount();
  });

  it("stops every surface when unmounted mid-load", async () => {
    const { root } = await render({ config: { weather: rainWeather, effects: true } });
    expect(pending.size).toBe(2);
    await act(async () => { root.unmount(); });
    expect(caf).toHaveBeenCalled();
    expect(pending.size).toBe(0);
  });
});
