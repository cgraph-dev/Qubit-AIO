/**
 * ReadAloudController — the "Read this to me" authority for `/qubit-chat`.
 *
 * One controller owns the whole per-message speech lifecycle so a second audio
 * element can never start while the first is still playing (no autoplay, at
 * most one utterance in flight). It reuses the SAME TTS transport the rest of
 * the dashboard already uses (`talk.speak` -> a `data:` audio URL rendered with
 * an `HTMLAudioElement`) rather than standing up a second TTS stack.
 *
 * The audio + synthesis transport is injected (`synthesize` / `player`) so the
 * state machine — start, replace, cancel-mid-playback, natural end, typed
 * failure — is exercised in tests without jsdom or a real audio device.
 *
 * Guarantees:
 *  - `start()` on a new message cancels whatever was playing first.
 *  - `cancel()` invokes the player's stop synchronously and clears state even
 *    if synthesis is still in flight (a late resolve is ignored via the
 *    generation guard).
 *  - a failed synthesis surfaces a typed error and never claims success.
 */

export interface ReadAloudPlayer {
  /** Begin playback of the audio URL; resolves when playback ends. */
  play(dataUrl: string): Promise<void>;
  /** Stop playback immediately (no-op when idle). */
  stop(): void;
}

export interface ReadAloudDeps {
  /** Synthesize `text` to an audio URL via the existing TTS route. */
  synthesize(text: string): Promise<string>;
  /** Create a player bound to one utterance. */
  createPlayer(): ReadAloudPlayer;
}

export interface ReadAloudSnapshot {
  /** The id of the message currently being read, or null. */
  speakingId: string | null;
  /** True from the moment a request is made until it ends/cancels/fails. */
  busy: boolean;
  error: string | null;
}

const IDLE: ReadAloudSnapshot = { speakingId: null, busy: false, error: null };

export class ReadAloudController {
  #deps: ReadAloudDeps;
  #listeners = new Set<(snapshot: ReadAloudSnapshot) => void>();
  #generation = 0;
  #player: ReadAloudPlayer | null = null;
  #snapshot: ReadAloudSnapshot = IDLE;

  constructor(deps: ReadAloudDeps) {
    this.#deps = deps;
  }

  getSnapshot(): ReadAloudSnapshot {
    return this.#snapshot;
  }

  subscribe(listener: (snapshot: ReadAloudSnapshot) => void): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  get active(): boolean {
    return this.#snapshot.busy;
  }

  /** Is THIS message the one currently being read? */
  isSpeaking(messageId: string): boolean {
    return this.#snapshot.speakingId === messageId;
  }

  /**
   * Speak `text` as `messageId`. Any in-flight utterance is cancelled first,
   * so pressing "read" on a second message replaces the first (single-audio).
   */
  async start(messageId: string, text: string): Promise<void> {
    const clean = (text ?? "").trim();
    // Cancel whatever was playing before replacing it.
    this.#cancelCurrent();
    if (!clean) return;
    const gen = (this.#generation += 1);
    this.#patch({ speakingId: messageId, busy: true, error: null });
    try {
      const dataUrl = await this.#deps.synthesize(clean);
      if (gen !== this.#generation) return; // superseded while synthesizing
      if (!dataUrl) {
        throw new Error("TTS returned no audio");
      }
      const player = this.#deps.createPlayer();
      this.#player = player;
      await player.play(dataUrl);
      if (gen !== this.#generation) return;
      this.#player = null;
      this.#patch(IDLE);
    } catch (err) {
      if (gen !== this.#generation) return;
      this.#player = null;
      this.#patch({
        speakingId: null,
        busy: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  /** Toggle: reading this message again stops it. */
  async toggle(messageId: string, text: string): Promise<void> {
    if (this.isSpeaking(messageId)) {
      this.cancel();
      return;
    }
    await this.start(messageId, text);
  }

  /** Stop playback immediately and clear state. Safe to call when idle. */
  cancel(): void {
    this.#cancelCurrent();
    this.#patch({ speakingId: null, busy: false, error: null });
  }

  dispose(): void {
    this.#generation += 1;
    this.#player?.stop();
    this.#player = null;
    this.#listeners.clear();
  }

  #cancelCurrent(): void {
    // Bump the generation so a pending synthesize/play resolve is ignored.
    this.#generation += 1;
    this.#player?.stop();
    this.#player = null;
  }

  #patch(patch: Partial<ReadAloudSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...patch };
    for (const listener of this.#listeners) listener(this.#snapshot);
  }
}

/** Wrap one `HTMLAudioElement` as a {@link ReadAloudPlayer}. */
export function audioElementPlayer(
  create: () => HTMLAudioElement = () => new Audio(),
): ReadAloudPlayer {
  let audio: HTMLAudioElement | null = null;
  return {
    play(dataUrl) {
      return new Promise<void>((resolve, reject) => {
        audio = create();
        const el = audio;
        el.src = dataUrl;
        el.onended = () => resolve();
        el.onerror = () => reject(new Error("audio playback failed"));
        // No autoplay: playback only ever starts because the user pressed a
        // read control, which this call is.
        const started = el.play();
        if (started && typeof started.catch === "function") {
          started.catch((e: unknown) =>
            reject(e instanceof Error ? e : new Error(String(e))),
          );
        }
      });
    },
    stop() {
      if (!audio) return;
      try {
        audio.pause();
        audio.currentTime = 0;
      } catch {
        /* element already detached — nothing to stop */
      }
      audio.onended = null;
      audio.onerror = null;
      audio = null;
    },
  };
}
