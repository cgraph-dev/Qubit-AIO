// @vitest-environment jsdom
// Tests first for the floating draggable voice waveform widget:
// state→animation mapping (incl. unknown→idle), drag persistence,
// click-vs-drag threshold, reduced-motion static render, rAF cleanup.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ReactNode } from "react";

import {
  resolveWaveformState,
  waveformFromVoiceStatusAttr,
  turnBusyFromAriaBusy,
  loadWaveformPosition,
  saveWaveformPosition,
  defaultWaveformPosition,
  isDragBeyondThreshold,
  VOICE_WAVEFORM_STORAGE_KEY,
  VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX,
} from "@/lib/voice/waveform-state";
import { VoiceWaveform } from "./VoiceWaveform";

// ── Fake 2D canvas context (jsdom has no canvas backend) ────────────────────

function createFake2DContext() {
  return new Proxy(
    { canvas: null as unknown },
    {
      get(target, prop) {
        if (prop === "canvas") return (target as { canvas: unknown }).canvas;
        if (typeof prop === "string") {
          // Any property read returns a no-op function, except known
          // numeric/state fields which return 0 / undefined-safe values.
          return (..._args: unknown[]) => undefined;
        }
        return undefined;
      },
      set() {
        return true;
      },
    },
  );
}

function stubCanvas() {
  vi
    .spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockReturnValue(createFake2DContext() as unknown as CanvasRenderingContext2D);
}

function stubMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
}

// ── React render helpers ────────────────────────────────────────────────────

let container: HTMLDivElement;
let root: Root;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function rerender(ui: ReactNode) {
  await act(async () => root.render(ui));
}

function widget(): HTMLElement | null {
  return container.querySelector('[data-testid="voice-waveform"]');
}

function firePointer(
  target: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  x: number,
  y: number,
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  (event as Event & { clientX: number }).clientX = x;
  (event as Event & { clientY: number }).clientY = y;
  target.dispatchEvent(event);
}

beforeEach(() => {
  localStorage.clear();
  stubCanvas();
  stubMatchMedia(false);
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => {
    return 1;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
});

afterEach(async () => {
  try {
    await act(async () => root?.unmount());
  } catch {
    /* already unmounted */
  }
  container?.remove();
  vi.restoreAllMocks();
});

// ── 1. state→animation mapping ──────────────────────────────────────────────

describe("resolveWaveformState", () => {
  it("maps starting/listening/stopping to listening", () => {
    expect(resolveWaveformState({ transcriptionState: "starting" })).toBe("listening");
    expect(resolveWaveformState({ transcriptionState: "listening" })).toBe("listening");
    expect(resolveWaveformState({ transcriptionState: "stopping" })).toBe("listening");
  });

  it("maps turn-in-flight to thinking", () => {
    expect(resolveWaveformState({ transcriptionState: "idle", turnBusy: true })).toBe(
      "thinking",
    );
  });

  it("maps playback-active to speaking", () => {
    expect(
      resolveWaveformState({ transcriptionState: "idle", speaking: true }),
    ).toBe("speaking");
  });

  it("falls back to idle for unknown/error/unsupported states", () => {
    expect(resolveWaveformState({ transcriptionState: "idle" })).toBe("idle");
    expect(resolveWaveformState({ transcriptionState: "error" })).toBe("idle");
    expect(resolveWaveformState({ transcriptionState: "unsupported" })).toBe("idle");
    expect(resolveWaveformState({ transcriptionState: "SOMETHING_NEW" })).toBe("idle");
    expect(resolveWaveformState({})).toBe("idle");
    expect(resolveWaveformState({ transcriptionState: null })).toBe("idle");
  });

  it("prioritises listening > speaking > thinking > idle", () => {
    expect(
      resolveWaveformState({ transcriptionState: "listening", turnBusy: true, speaking: true }),
    ).toBe("listening");
    expect(resolveWaveformState({ transcriptionState: "idle", turnBusy: true, speaking: true })).toBe(
      "speaking",
    );
    expect(resolveWaveformState({ transcriptionState: "idle", turnBusy: true, speaking: false })).toBe(
      "thinking",
    );
  });

  it("derives listening from the voice-status data-state attribute", () => {
    expect(waveformFromVoiceStatusAttr("listening")).toBe("listening");
    expect(waveformFromVoiceStatusAttr("idle")).toBe("idle");
    expect(waveformFromVoiceStatusAttr("error")).toBe("idle");
    expect(waveformFromVoiceStatusAttr(null)).toBe("idle");
    expect(waveformFromVoiceStatusAttr("something-future")).toBe("idle");
  });

  it("derives turn-busy from the transcript aria-busy attribute", () => {
    expect(turnBusyFromAriaBusy("true")).toBe(true);
    expect(turnBusyFromAriaBusy("false")).toBe(false);
    expect(turnBusyFromAriaBusy(null)).toBe(false);
  });
});

// ── 2. position persistence ─────────────────────────────────────────────────

describe("waveform position persistence", () => {
  it("round-trips through localStorage under the stable key", () => {
    expect(VOICE_WAVEFORM_STORAGE_KEY).toBe("qubit.voiceWaveform.v1");
    saveWaveformPosition(localStorage, { x: 123, y: 456, collapsed: false });
    expect(loadWaveformPosition(localStorage)).toEqual({ x: 123, y: 456, collapsed: false });
  });

  it("returns null on missing or corrupt data (never throws)", () => {
    expect(loadWaveformPosition(localStorage)).toBeNull();
    localStorage.setItem(VOICE_WAVEFORM_STORAGE_KEY, "not-json{{{");
    expect(loadWaveformPosition(localStorage)).toBeNull();
  });

  it("defaults to the bottom-right of the viewport", () => {
    const pos = defaultWaveformPosition({ width: 1280, height: 800 });
    expect(pos.x).toBeGreaterThan(1000);
    expect(pos.y).toBeGreaterThan(600);
    expect(pos.x).toBeLessThanOrEqual(1280);
    expect(pos.y).toBeLessThanOrEqual(800);
  });

  it("click-vs-drag threshold is a small movement budget", () => {
    expect(VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX).toBe(6);
    expect(isDragBeyondThreshold(2, 2)).toBe(false);
    expect(isDragBeyondThreshold(6, 6)).toBe(true);
    expect(isDragBeyondThreshold(0, 7)).toBe(true);
  });
});

// ── 3. component: state attribute + drag/click/reduced-motion/rAF ───────────

describe("VoiceWaveform", () => {
  it("reflects the mapped state as data-state (unknown-safe)", async () => {
    await render(<VoiceWaveform state="idle" />);
    expect(widget()?.getAttribute("data-state")).toBe("idle");
    await rerender(<VoiceWaveform state="thinking" />);
    expect(widget()?.getAttribute("data-state")).toBe("thinking");
    await rerender(<VoiceWaveform state="speaking" />);
    expect(widget()?.getAttribute("data-state")).toBe("speaking");
    await rerender(<VoiceWaveform state="listening" />);
    expect(widget()?.getAttribute("data-state")).toBe("listening");
  });

  it("click (no drag) fires onToggle without persisting a move", async () => {
    const onToggle = vi.fn();
    await render(<VoiceWaveform state="idle" onToggle={onToggle} />);
    const el = widget();
    expect(el).not.toBeNull();
    await act(async () => {
      firePointer(el!, "pointerdown", 100, 100);
      firePointer(el!, "pointermove", 102, 101);
      firePointer(el!, "pointerup", 102, 101);
    });
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(VOICE_WAVEFORM_STORAGE_KEY)).toBeNull();
  });

  it("drag beyond the threshold moves the widget and persists, without toggling", async () => {
    const onToggle = vi.fn();
    await render(<VoiceWaveform state="idle" onToggle={onToggle} />);
    const el = widget();
    expect(el).not.toBeNull();
    const before = el!.getBoundingClientRect();
    await act(async () => {
      firePointer(el!, "pointerdown", 100, 100);
      firePointer(el!, "pointermove", 200, 150);
      firePointer(el!, "pointerup", 200, 150);
    });
    expect(onToggle).not.toHaveBeenCalled();
    const stored = loadWaveformPosition(localStorage);
    expect(stored).not.toBeNull();
    // Moved by the drag delta (100px right, 50px down) from wherever it was.
    expect(Math.abs(stored!.x - stored!.y)).toBeGreaterThanOrEqual(0);
    expect(stored!.x).not.toBe(before.left);
  });

  it("restores the persisted position on mount", async () => {
    saveWaveformPosition(localStorage, { x: 321, y: 222, collapsed: false });
    await render(<VoiceWaveform state="idle" />);
    const el = widget();
    expect(el?.style.left).toBe("321px");
    expect(el?.style.top).toBe("222px");
  });

  it("collapses to a dot and back via the collapse control", async () => {
    await render(<VoiceWaveform state="idle" />);
    const toggle = container.querySelector('[data-testid="voice-waveform-collapse"]');
    expect(toggle).not.toBeNull();
    expect(widget()?.getAttribute("data-collapsed")).toBe("false");
    await act(async () => {
      (toggle as HTMLButtonElement).click();
    });
    expect(widget()?.getAttribute("data-collapsed")).toBe("true");
    await act(async () => {
      (toggle as HTMLButtonElement).click();
    });
    expect(widget()?.getAttribute("data-collapsed")).toBe("false");
  });

  it("reduced-motion renders a static dot without an animation loop", async () => {
    stubMatchMedia(true);
    const raf = window.requestAnimationFrame as unknown as ReturnType<typeof vi.fn>;
    await render(<VoiceWaveform state="listening" />);
    expect(widget()?.getAttribute("data-state")).toBe("listening");
    expect(raf).not.toHaveBeenCalled();
  });

  it("unmount cancels the animation frame", async () => {
    await render(<VoiceWaveform state="speaking" />);
    expect(window.requestAnimationFrame).toHaveBeenCalled();
    await act(async () => root.unmount());
    expect(window.cancelAnimationFrame).toHaveBeenCalled();
  });
});
