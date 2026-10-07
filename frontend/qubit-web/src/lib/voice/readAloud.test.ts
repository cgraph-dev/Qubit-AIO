/** ReadAloudController — the "Read this to me" state machine. Pure deps, no DOM. */
import { describe, expect, it, vi } from "vitest";

import {
  ReadAloudController,
  type ReadAloudDeps,
  type ReadAloudPlayer,
} from "./readAloud";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakePlayer() {
  const stop = vi.fn();
  const play = vi.fn<(url: string) => Promise<void>>();
  const player: ReadAloudPlayer = { play, stop };
  const deps: ReadAloudDeps = {
    synthesize: vi.fn(async (t: string) => `data:audio/wav;base64,${t.length}`),
    createPlayer: () => player,
  };
  return { deps, play, stop };
}

describe("ReadAloudController", () => {
  it("speaks a message and returns to idle on natural end", async () => {
    const { deps, play } = fakePlayer();
    play.mockResolvedValue();
    const c = new ReadAloudController(deps);
    await c.start("m1", "hello");
    expect(play).toHaveBeenCalledWith("data:audio/wav;base64,5");
    expect(c.getSnapshot()).toEqual({ speakingId: null, busy: false, error: null });
  });

  it("marks busy + speakingId while playing", async () => {
    const { deps, play } = fakePlayer();
    const gate = deferred<void>();
    play.mockReturnValue(gate.promise);
    const c = new ReadAloudController(deps);
    const seen: string[] = [];
    c.subscribe((s) => seen.push(String(s.speakingId)));
    const p = c.start("m1", "hello");
    expect(c.getSnapshot().busy).toBe(true);
    expect(c.isSpeaking("m1")).toBe(true);
    gate.resolve();
    await p;
    expect(c.isSpeaking("m1")).toBe(false);
    expect(seen).toContain("m1");
  });

  it("cancel() stops playback mid-flight and clears state", async () => {
    const { deps, play, stop } = fakePlayer();
    const gate = deferred<void>();
    play.mockReturnValue(gate.promise);
    const c = new ReadAloudController(deps);
    const p = c.start("m1", "long text");
    // Let synthesize resolve so the player exists.
    await Promise.resolve();
    await Promise.resolve();
    c.cancel();
    expect(stop).toHaveBeenCalledTimes(1);
    expect(c.getSnapshot()).toEqual({ speakingId: null, busy: false, error: null });
    gate.resolve();
    await p; // a late resolve after cancel must not reopen the utterance
    expect(c.getSnapshot().busy).toBe(false);
  });

  it("starting a second message replaces the first (single audio)", async () => {
    const { deps, play, stop } = fakePlayer();
    // Hold the FIRST utterance open: `play()` resolving naturally clears the
    // player, so a naive `mockResolvedValue()` leaves nothing for the second
    // `start()` to stop and the assertion below would never describe real
    // "replace while still speaking" behaviour.
    const gate = deferred<void>();
    play.mockReturnValueOnce(gate.promise);
    const c = new ReadAloudController(deps);
    const first = c.start("m1", "one");
    // Let synthesize resolve so the player exists and is still speaking.
    await Promise.resolve();
    await Promise.resolve();
    play.mockResolvedValue();
    await c.start("m2", "two");
    expect(stop).toHaveBeenCalledTimes(1);
    expect(c.getSnapshot().speakingId).toBeNull();
    gate.resolve();
    await first; // the superseded utterance must not reopen state
    expect(c.getSnapshot().speakingId).toBeNull();
  });

  it("toggle on the speaking message stops it", async () => {
    const { deps, play, stop } = fakePlayer();
    const gate = deferred<void>();
    play.mockReturnValue(gate.promise);
    const c = new ReadAloudController(deps);
    void c.start("m1", "hello");
    await Promise.resolve();
    await Promise.resolve();
    await c.toggle("m1", "hello");
    expect(stop).toHaveBeenCalled();
    expect(c.getSnapshot().busy).toBe(false);
  });

  it("a failed synthesis surfaces a typed error, never a false success", async () => {
    const deps: ReadAloudDeps = {
      synthesize: vi.fn(async () => {
        throw new Error("AUDIO_TTS_UNAVAILABLE");
      }),
      createPlayer: () => ({ play: vi.fn(), stop: vi.fn() }),
    };
    const c = new ReadAloudController(deps);
    await c.start("m1", "hello");
    expect(c.getSnapshot().busy).toBe(false);
    expect(c.getSnapshot().error).toBe("AUDIO_TTS_UNAVAILABLE");
  });

  it("empty text is a no-op (no synthesis, no audio)", async () => {
    const { deps } = fakePlayer();
    const c = new ReadAloudController(deps);
    await c.start("m1", "   ");
    expect(deps.synthesize).not.toHaveBeenCalled();
    expect(c.getSnapshot().busy).toBe(false);
  });

  it("an empty TTS data URL is treated as a failure", async () => {
    const deps: ReadAloudDeps = {
      synthesize: vi.fn(async () => ""),
      createPlayer: () => ({ play: vi.fn(), stop: vi.fn() }),
    };
    const c = new ReadAloudController(deps);
    await c.start("m1", "hello");
    expect(c.getSnapshot().error).toMatch(/no audio/i);
  });
});
