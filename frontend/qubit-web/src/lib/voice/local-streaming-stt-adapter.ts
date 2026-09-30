/**
 * LocalStreamingSTTAdapter — capture microphone PCM in the browser and stream
 * short frames to Qubit's authenticated STT WebSocket (`/api/audio/stt/ws`).
 *
 * The socket drives the ONE Qubit local ASR authority (sherpa-onnx Moonshine /
 * Parakeet offline, or a streaming zipformer/Nemotron pack). Audio is converted
 * to mono 16 kHz PCM and sent ~every 200–400 ms; partials arrive live.
 *
 * All external seams are injectable so the controller tests never touch real
 * WebAudio or a real socket.
 */
import { api } from "../api";
import type { AdapterEvents, TranscriptionAdapter } from "./types";

export interface SttSocket {
  binaryType: string;
  send(data: string | ArrayBuffer): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null;
}

export interface PcmCapture {
  start(): void;
  stop(): void;
}

export interface LocalAdapterDeps {
  getWsUrl?: () => Promise<string>;
  openSocket?: (url: string) => SttSocket;
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  createCapture?: (
    stream: MediaStream,
    onFrame: (pcm: Int16Array) => void,
  ) => PcmCapture;
  /** The canonical chat session id, carried on the STT request for continuity. */
  sessionId?: () => string;
}

const TARGET_SR = 16000;
/** ~300 ms of mono 16 kHz PCM per frame (budget 200–400 ms). */
const FRAME_SAMPLES = Math.round(TARGET_SR * 0.3);
/** Client-side gate: don't stream pure leading silence. */
const SILENCE_RMS = 0.004;
const STOP_TIMEOUT_MS = 4000;

export const LOCAL_PRIVACY_NOTICE =
  "local STT keeps audio on the configured local machine";

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `stt-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

function floatToInt16(input: Float32Array, srcRate: number): Int16Array {
  const data =
    srcRate === TARGET_SR ? input : resampleLinear(input, srcRate, TARGET_SR);
  const out = new Int16Array(data.length);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function resampleLinear(
  input: Float32Array,
  srcRate: number,
  dstRate: number,
): Float32Array {
  if (srcRate === dstRate || input.length === 0) return input;
  const nOut = Math.round((input.length * dstRate) / srcRate);
  const out = new Float32Array(nOut);
  const step = (input.length - 1) / Math.max(1, nOut - 1);
  for (let i = 0; i < nOut; i++) {
    const pos = i * step;
    const lo = Math.floor(pos);
    const hi = Math.min(input.length - 1, lo + 1);
    const frac = pos - lo;
    out[i] = input[lo] * (1 - frac) + input[hi] * frac;
  }
  return out;
}

function rms(pcm: Int16Array): number {
  if (pcm.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) {
    const v = pcm[i] / 32768;
    sum += v * v;
  }
  return Math.sqrt(sum / pcm.length);
}

/** Default WebAudio PCM capture (mono, resampled to 16 kHz). */
function defaultCreateCapture(
  stream: MediaStream,
  onFrame: (pcm: Int16Array) => void,
): PcmCapture {
  const AC: typeof AudioContext | undefined =
    typeof AudioContext !== "undefined"
      ? AudioContext
      : (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;
  if (!AC) throw new Error("NO_AUDIO_CONTEXT");
  const ctx = new AC({ sampleRate: TARGET_SR });
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  processor.onaudioprocess = (event: AudioProcessingEvent) => {
    const input = event.inputBuffer.getChannelData(0);
    onFrame(floatToInt16(input, ctx.sampleRate));
  };
  source.connect(processor);
  processor.connect(ctx.destination);
  return {
    start() {
      // A context created under a user gesture usually runs, but resume() is
      // required when the browser autoplayed it in "suspended" state.
      void ctx.resume().catch(() => undefined);
    },
    stop() {
      try {
        processor.disconnect();
        source.disconnect();
      } catch {
        /* already torn down */
      }
      void ctx.close().catch(() => undefined);
    },
  };
}

export class LocalSttUnavailable extends Error {
  readonly code: string;
  constructor(code: string, detail: string) {
    super(detail);
    this.code = code;
  }
}

export class LocalStreamingSTTAdapter implements TranscriptionAdapter {
  readonly kind: "local" | "terminal";
  readonly label: string;

  #socket: SttSocket | null = null;
  #capture: PcmCapture | null = null;
  #stream: MediaStream | null = null;
  #events: AdapterEvents | null = null;
  #requestId = "";
  #sequence = 0;
  #pending: Int16Array[] = [];
  #pendingSamples = 0;
  #speechSeen = false;
  #stopped = false;
  #closed = false;
  #endResolve: (() => void) | null = null;
  #readyResolve: (() => void) | null = null;
  #readyReject: ((code: string, detail: string) => void) | null = null;
  #finalText = "";
  #engineLabel: string;
  #deps: LocalAdapterDeps;

  constructor(
    deps: LocalAdapterDeps = {},
    opts: { kind?: "local" | "terminal"; label?: string } = {},
  ) {
    this.#deps = deps;
    this.kind = opts.kind ?? "local";
    this.label = opts.label ?? this.kind;
    this.#engineLabel = this.label;
  }

  isAvailable(): boolean {
    if (this.#deps.getUserMedia || this.#deps.createCapture) return true;
    if (typeof navigator === "undefined") return false;
    const md = navigator.mediaDevices as MediaDevices | undefined;
    return typeof md?.getUserMedia === "function";
  }

  availabilityReason(): string | null {
    return this.isAvailable()
      ? null
      : "microphone capture (getUserMedia) is unavailable";
  }

  async start(events: AdapterEvents): Promise<void> {
    if (!this.isAvailable()) {
      throw new LocalSttUnavailable(
        "VOICE_NO_MIC",
        "microphone capture is unavailable in this browser",
      );
    }
    this.#events = events;
    this.#stopped = false;
    this.#closed = false;
    this.#speechSeen = false;
    this.#pending = [];
    this.#pendingSamples = 0;
    this.#sequence = 0;
    this.#finalText = "";
    this.#requestId = uuid();

    const getMedia =
      this.#deps.getUserMedia ??
      ((c: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(c));
    const getUrl = this.#deps.getWsUrl ?? (() => api.sttWsUrl());
    const openSocket =
      this.#deps.openSocket ??
      ((url: string) => new WebSocket(url) as unknown as SttSocket);
    const createCapture = this.#deps.createCapture ?? defaultCreateCapture;

    let stream: MediaStream;
    try {
      // English speech capture conditioning: mono, with browser noise
      // suppression / echo cancellation / AGC where the browser supports them.
      stream = await getMedia({
        audio: {
          channelCount: 1,
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true,
        },
      });
    } catch {
      throw new LocalSttUnavailable(
        "VOICE_DENIED",
        "microphone blocked or unavailable",
      );
    }
    this.#stream = stream;

    let url: string;
    try {
      url = await getUrl();
    } catch {
      this.#teardownStream();
      throw new LocalSttUnavailable(
        "VOICE_STT_UNAVAILABLE",
        "could not mint an STT socket ticket",
      );
    }

    const socket = openSocket(url);
    socket.binaryType = "arraybuffer";
    this.#socket = socket;
    const opened = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new LocalSttUnavailable("VOICE_STT_TIMEOUT",
          "the local STT socket did not open")),
        STOP_TIMEOUT_MS,
      );
      socket.onopen = () => {
        clearTimeout(timer);
        events.onConnected?.();
        this.#sendJson({
          type: "start",
          session_id: this.#deps.sessionId?.() ?? "qubit-chat",
          request_id: this.#requestId,
          format: "pcm_s16le",
          sample_rate: TARGET_SR,
          sequence: this.#sequence++,
        });
        resolve();
      };
      socket.onerror = () => {
        clearTimeout(timer);
        reject(new LocalSttUnavailable("VOICE_STT_ERROR",
          "the local STT socket failed to open"));
      };
    });
    // Wait for `stt.started` so a missing local model surfaces as a typed
    // error at start() (before the mic is opened for capture).
    const ready = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new LocalSttUnavailable("VOICE_STT_TIMEOUT",
          "the local STT backend did not acknowledge the request")),
        STOP_TIMEOUT_MS,
      );
      this.#readyResolve = () => {
        clearTimeout(timer);
        resolve();
      };
      this.#readyReject = (code, detail) => {
        clearTimeout(timer);
        reject(new LocalSttUnavailable(code, detail));
      };
    });
    socket.onmessage = (ev) => this.#onMessage(ev.data);
    socket.onclose = () => {
      if (!this.#stopped && !this.#closed) {
        this.#events?.onError("VOICE_STT_DISCONNECTED",
          "the local STT socket disconnected");
      }
      this.#readyReject?.("VOICE_STT_DISCONNECTED",
        "the local STT socket closed before it started");
      this.#endResolve?.();
    };

    try {
      await opened;
      await ready;
    } catch (err) {
      this.#teardown();
      throw err;
    }

    const capture = createCapture(stream, (pcm) => this.#onFrame(pcm));
    this.#capture = capture;
    capture.start();
    events.onEngine?.(this.#engineLabel);
  }

  async stop(): Promise<void> {
    if (this.#stopped || !this.#socket) return;
    this.#stopped = true;
    this.#capture?.stop();
    this.#capture = null;
    this.#flush(true);
    const done = new Promise<void>((resolve) => {
      this.#endResolve = resolve;
    });
    this.#sendJson({ type: "stop", sequence: this.#sequence++ });
    if (this.#finalText.trim()) {
      this.#events?.onFinal(this.#finalText.trim());
      this.#finalText = "";
    }
    await Promise.race([
      done,
      new Promise<void>((r) => setTimeout(r, STOP_TIMEOUT_MS)),
    ]);
    this.#teardown();
  }

  async cancel(): Promise<void> {
    this.#closed = true;
    try {
      this.#sendJson({ type: "cancel", sequence: this.#sequence++ });
    } catch {
      /* socket may already be closed */
    }
    this.#teardown();
  }

  dispose(): void {
    this.#teardown();
    this.#events = null;
  }

  // -- internals -------------------------------------------------------
  #onFrame(pcm: Int16Array): void {
    if (this.#stopped || this.#closed) return;
    if (!this.#speechSeen) {
      if (rms(pcm) < SILENCE_RMS) return; // skip leading silence
      this.#speechSeen = true;
    }
    this.#pending.push(pcm);
    this.#pendingSamples += pcm.length;
    if (this.#pendingSamples >= FRAME_SAMPLES) this.#flush(false);
  }

  #flush(force: boolean): void {
    if (!this.#socket || this.#pending.length === 0) return;
    if (!force && this.#pendingSamples < FRAME_SAMPLES) return;
    const total = this.#pendingSamples;
    const merged = new Int16Array(total);
    let offset = 0;
    for (const chunk of this.#pending) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    this.#pending = [];
    this.#pendingSamples = 0;
    const header = new ArrayBuffer(4);
    new DataView(header).setUint32(0, this.#sequence++, false);
    const payload = new Uint8Array(4 + merged.byteLength);
    payload.set(new Uint8Array(header), 0);
    payload.set(new Uint8Array(merged.buffer), 4);
    try {
      this.#socket.send(payload.buffer);
    } catch {
      this.#events?.onError("VOICE_STT_SEND_FAILED",
        "could not send audio to the local STT socket");
    }
  }

  #sendJson(obj: Record<string, unknown>): void {
    this.#socket?.send(JSON.stringify(obj));
  }

  #onMessage(data: unknown): void {
    if (typeof data !== "string") return;
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(data) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = String(msg.type ?? "");
    // Stale request events (from a superseded utterance) are ignored.
    if (String(msg.request_id ?? "") !== this.#requestId) return;
    switch (type) {
      case "stt.started":
        if (typeof msg.engine === "string" && msg.engine) {
          this.#engineLabel = msg.engine;
          this.#events?.onEngine?.(this.#engineLabel);
        }
        this.#readyResolve?.();
        break;
      case "stt.partial":
        this.#events?.onPartial(String(msg.text ?? ""));
        break;
      case "stt.final":
        this.#finalText = String(msg.text ?? "");
        break;
      case "stt.completed": {
        const metrics = msg.metrics as Record<string, unknown> | undefined;
        if (metrics) {
          this.#events?.onMetrics?.({
            audioMs: Number(metrics.audio_ms) || 0,
            timeToFirstPartialMs:
              metrics.time_to_first_partial_ms == null
                ? null
                : Number(metrics.time_to_first_partial_ms),
            finalizationMs:
              metrics.finalization_ms == null
                ? null
                : Number(metrics.finalization_ms),
          });
        }
        if (msg.cancelled === true) {
          this.#endResolve?.();
        } else if (this.#stopped) {
          if (this.#finalText.trim()) {
            this.#events?.onFinal(this.#finalText.trim());
            this.#finalText = "";
          }
          this.#endResolve?.();
          this.#events?.onEnd?.();
        }
        break;
      }
      case "stt.error":
        this.#readyReject?.(
          String(msg.code ?? "VOICE_STT_ERROR"),
          String(msg.detail ?? "local STT failed"),
        );
        this.#events?.onError(String(msg.code ?? "VOICE_STT_ERROR"),
          String(msg.detail ?? "local STT failed"));
        this.#endResolve?.();
        break;
      default:
        break;
    }
  }

  #teardownStream(): void {
    const stream = this.#stream;
    this.#stream = null;
    try {
      stream?.getTracks().forEach((t) => t.stop());
    } catch {
      /* already stopped */
    }
  }

  #teardown(): void {
    this.#capture?.stop();
    this.#capture = null;
    this.#teardownStream();
    this.#readyResolve = null;
    this.#readyReject = null;
    const socket = this.#socket;
    this.#socket = null;
    try {
      socket?.close();
    } catch {
      /* already closed */
    }
  }
}
