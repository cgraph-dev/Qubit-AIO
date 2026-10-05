// @vitest-environment jsdom
/** WaveformOverlayMount — dashboard waveform gated by voice_edit.waveform_visible. */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WaveformOverlayMount } from "./WaveformOverlayMount";

const mocks = vi.hoisted(() => ({
  settings: vi.fn(),
}));

vi.mock("@/lib/voiceEdit", () => ({
  voiceEdit: { settings: mocks.settings },
}));

vi.mock("@/components/VoiceWaveformHost", () => ({
  VoiceWaveformHost: () => <div data-testid="voice-waveform-host" />,
}));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function renderAt(path: string) {
  root.render(
    <MemoryRouter initialEntries={[path]}>
      <WaveformOverlayMount />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("WaveformOverlayMount", () => {
  it("mounts the waveform host when the setting is visible", async () => {
    mocks.settings.mockResolvedValue({ settings: { waveform_visible: true } });
    await act(async () => {
      renderAt("/");
    });
    await flush();
    expect(
      container.querySelector('[data-testid="voice-waveform-host"]'),
    ).toBeTruthy();
  });

  it("does not mount the host when the setting hides it", async () => {
    mocks.settings.mockResolvedValue({ settings: { waveform_visible: false } });
    await act(async () => {
      renderAt("/");
    });
    await flush();
    expect(
      container.querySelector('[data-testid="voice-waveform-host"]'),
    ).toBeNull();
  });

  it("stays hidden by default when the settings fetch fails", async () => {
    // Owner 2026-10-04: hidden default (OS orb is the only always-on
    // waveform). Deliberate reversal of the old visible-on-error pin.
    mocks.settings.mockRejectedValue(new Error("down"));
    await act(async () => {
      renderAt("/");
    });
    await flush();
    expect(
      container.querySelector('[data-testid="voice-waveform-host"]'),
    ).toBeNull();
  });

  it("stays hidden when the key is absent (fresh config)", async () => {
    mocks.settings.mockResolvedValue({ settings: {} });
    await act(async () => {
      renderAt("/");
    });
    await flush();
    expect(
      container.querySelector('[data-testid="voice-waveform-host"]'),
    ).toBeNull();
  });

  it("stays hidden on /qubit-chat even when the setting is visible", async () => {
    // The chat route has its own voice UI; the floating widget never mounts
    // there. A visible setting must not leak the host onto this route.
    mocks.settings.mockResolvedValue({ settings: { waveform_visible: true } });
    await act(async () => {
      renderAt("/qubit-chat");
    });
    await flush();
    expect(
      container.querySelector('[data-testid="voice-waveform-host"]'),
    ).toBeNull();
  });

  it("reacts live to a voice flip without reload (2026-10-04)", async () => {
    // Owner: "hide the waveform" flipped the backend but a loaded page kept
    // showing the widget until reload. The gate re-reads every 5 s now.
    vi.useFakeTimers();
    try {
      mocks.settings.mockResolvedValue({ settings: { waveform_visible: true } });
      await act(async () => {
        renderAt("/");
      });
      await flush();
      expect(
        container.querySelector('[data-testid="voice-waveform-host"]'),
      ).toBeTruthy();
      // Voice "hide the waveform" flips the backend; next poll unmounts.
      mocks.settings.mockResolvedValue({ settings: { waveform_visible: false } });
      await act(async () => {
        vi.advanceTimersByTime(5000);
      });
      await flush();
      expect(
        container.querySelector('[data-testid="voice-waveform-host"]'),
      ).toBeNull();
      // And "show the waveform" re-mounts the same way.
      mocks.settings.mockResolvedValue({ settings: { waveform_visible: true } });
      await act(async () => {
        vi.advanceTimersByTime(5000);
      });
      await flush();
      expect(
        container.querySelector('[data-testid="voice-waveform-host"]'),
      ).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling after unmount (no interval leak)", async () => {
    vi.useFakeTimers();
    try {
      mocks.settings.mockResolvedValue({ settings: { waveform_visible: false } });
      await act(async () => {
        renderAt("/");
      });
      await flush();
      const callsAfterMount = mocks.settings.mock.calls.length;
      act(() => root.unmount());
      await act(async () => {
        vi.advanceTimersByTime(15000);
      });
      // No further settings reads once unmounted.
      expect(mocks.settings.mock.calls.length).toBe(callsAfterMount);
    } finally {
      vi.useRealTimers();
    }
  });
});
