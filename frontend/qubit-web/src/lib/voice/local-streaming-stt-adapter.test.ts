// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import {
  LocalStreamingSTTAdapter,
  resampleLinear,
  type PcmCapture,
  type SttSocket,
} from "./local-streaming-stt-adapter";

interface FakeSocket extends SttSocket {
  sent: unknown[];
  closed: boolean;
}

function fakeSocket(): FakeSocket {
  return {
    binaryType: "",
    sent: [],
    closed: false,
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
    send(data: unknown) {
      this.sent.push(data);
    },
    close() {
      this.closed = true;
    },
  };
}

function decodeBinaryFrame(frame: ArrayBuffer): {
  sequence: number;
  samples: number;
} {
  const view = new DataView(frame);
  return {
    sequence: view.getUint32(0, false),
    samples: (frame.byteLength - 4) / 2,
  };
}

async function startAdapter(
  deps: {
    socket: FakeSocket;
    onFrame?: (cb: (pcm: Int16Array) => void) => void;
  },
  overrides: Partial<ConstructorParameters<typeof LocalStreamingSTTAdapter>[0]> = {},
) {
  const captureHolder: { frameCb: ((pcm: Int16Array) => void) | null } = {
    frameCb: null,
  };
  const adapter = new LocalStreamingSTTAdapter({
    getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
    openSocket: () => deps.socket,
    getUserMedia: async () => ({}) as MediaStream,
    createCapture: (_stream, onFrame): PcmCapture => {
      captureHolder.frameCb = onFrame;
      deps.onFrame?.(onFrame);
      return { start: () => undefined, stop: () => undefined };
    },
    ...overrides,
  });
  const partials: string[] = [];
  const finals: string[] = [];
  const errors: Array<[string, string]> = [];
  const engines: string[] = [];
  const startP = adapter.start({
    onPartial: (t) => partials.push(t),
    onFinal: (t) => finals.push(t),
    onError: (c, d) => errors.push([c, d]),
    onEngine: (l) => engines.push(l),
  });
  await vi.waitFor(() => expect(deps.socket.onopen).not.toBeNull());
  deps.socket.onopen?.({});
  // The server acknowledges with `stt.started`; start() now waits for it.
  await vi.waitFor(() => expect(deps.socket.sent.length).toBeGreaterThan(0));
  const requestId = (
    JSON.parse(String(deps.socket.sent[0])) as { request_id: string }
  ).request_id;
  deps.socket.onmessage?.({
    data: JSON.stringify({
      type: "stt.started",
      request_id: requestId,
      engine: "local-window",
    }),
  });
  await startP;
  return {
    adapter,
    socket: deps.socket,
    requestId,
    partials,
    finals,
    errors,
    engines,
    frame: (pcm: Int16Array) => captureHolder.frameCb?.(pcm),
  };
}

describe("LocalStreamingSTTAdapter", () => {
  it("sends a start frame then streams PCM frames", async () => {
    const socket = fakeSocket();
    const { frame } = await startAdapter({ socket });
    const start = JSON.parse(String(socket.sent[0])) as Record<string, unknown>;
    expect(start.type).toBe("start");
    expect(start.format).toBe("pcm_s16le");
    expect(start.sample_rate).toBe(16000);
    expect(typeof start.request_id).toBe("string");
    // ~300 ms of speech flushes one binary frame [uint32 seq][pcm16].
    frame(new Int16Array(4800).fill(1200));
    const binary = socket.sent.find((s) => s instanceof ArrayBuffer);
    expect(binary).toBeInstanceOf(ArrayBuffer);
    const decoded = decodeBinaryFrame(binary as ArrayBuffer);
    expect(decoded.samples).toBe(4800);
  });

  it("handles partial/final/completed and commits on stop", async () => {
    const socket = fakeSocket();
    const { adapter, partials, finals, frame } = await startAdapter({ socket });
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    frame(new Int16Array(4800).fill(1200));
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.partial",
        request_id: requestId,
        text: "helo",
      }),
    });
    expect(partials).toEqual(["helo"]);
    const stopP = adapter.stop();
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.final",
        request_id: requestId,
        text: "hello world",
      }),
    });
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.completed",
        request_id: requestId,
        cancelled: false,
        final_text: "hello world",
        metrics: { audio_ms: 300 },
      }),
    });
    await stopP;
    expect(finals).toEqual(["hello world"]);
  });

  it("ignores stale request events", async () => {
    const socket = fakeSocket();
    const { adapter, partials } = await startAdapter({ socket });
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.partial",
        request_id: "some-other-request",
        text: "stale",
      }),
    });
    expect(partials).toEqual([]);
    void adapter;
  });

  it("surfaces a typed error on socket disconnect (and stays recoverable)", async () => {
    const socket = fakeSocket();
    const { errors } = await startAdapter({ socket });
    socket.onclose?.({ code: 1006, reason: "abnormal" });
    expect(errors.some(([c]) => c === "VOICE_STT_DISCONNECTED")).toBe(true);
  });

  it("reports microphone denial as VOICE_DENIED", async () => {
    const socket = fakeSocket();
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => "ws://x",
      openSocket: () => socket,
      getUserMedia: async () => {
        throw new Error("NotAllowedError");
      },
      createCapture: () => ({ start: () => undefined, stop: () => undefined }),
    });
    await expect(
      adapter.start({
        onPartial: () => undefined,
        onFinal: () => undefined,
        onError: () => undefined,
      }),
    ).rejects.toMatchObject({ code: "VOICE_DENIED" });
  });

  it("surfaces a typed failure at start when the backend refuses", async () => {
    const socket = fakeSocket();
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => "ws://x",
      openSocket: () => socket,
      getUserMedia: async () => ({}) as MediaStream,
      createCapture: () => ({ start: () => undefined, stop: () => undefined }),
    });
    const errors: Array<[string, string]> = [];
    const startP = adapter.start({
      onPartial: () => undefined,
      onFinal: () => undefined,
      onError: (c, d) => errors.push([c, d]),
    });
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.onopen?.({});
    await vi.waitFor(() => expect(socket.sent.length).toBeGreaterThan(0));
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.error",
        request_id: requestId,
        code: "STT_UNAVAILABLE",
        detail: "no local model installed",
      }),
    });
    await expect(startP).rejects.toMatchObject({ code: "STT_UNAVAILABLE" });
    expect(errors.some(([c]) => c === "STT_UNAVAILABLE")).toBe(true);
  });

  it("does not stream leading silence", async () => {
    const socket = fakeSocket();
    const { frame } = await startAdapter({ socket });
    frame(new Int16Array(4800)); // all-zero PCM = silence
    expect(socket.sent.find((s) => s instanceof ArrayBuffer)).toBeUndefined();
  });
});

describe("resampleLinear", () => {
  it("resamples 48 kHz to 16 kHz length correctly", () => {
    const input = new Float32Array(4800);
    expect(resampleLinear(input, 48000, 16000).length).toBe(1600);
    expect(resampleLinear(input, 16000, 16000)).toBe(input);
  });
});
