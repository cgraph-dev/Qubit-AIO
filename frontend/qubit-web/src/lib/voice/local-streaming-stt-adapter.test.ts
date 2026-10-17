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

  it("surfaces the server's no-transcript warning instead of a silent turn", async () => {
    const socket = fakeSocket();
    const { adapter, errors, finals } = await startAdapter({ socket });
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    const stopP = adapter.stop();
    // Server stopped with no transcript and explained why (nothing buffered
    // vs rescore failure) — the turn must say so, not go quiet.
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.completed",
        request_id: requestId,
        cancelled: false,
        final_text: "",
        warning_code: "STT_NO_TRANSCRIPT",
        warning: "no buffered audio",
        metrics: { audio_ms: 0 },
      }),
    });
    await stopP;
    expect(finals).toEqual([]);
    expect(errors).toEqual([["STT_NO_TRANSCRIPT", "no buffered audio"]]);
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

  it("starts capture before the socket is ready (no onset gap)", async () => {
    const socket = fakeSocket();
    let captureCreated = false;
    let readySeen = false;
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
      openSocket: () => socket,
      getUserMedia: async () => ({}) as MediaStream,
      createCapture: (_stream, onFrame): PcmCapture => {
        captureCreated = true;
        void onFrame;
        return { start: () => undefined, stop: () => undefined };
      },
    });
    const startP = adapter.start({
      onPartial: () => undefined,
      onFinal: () => undefined,
      onError: () => undefined,
    });
    // Capture must open on the button-press edge — before the socket
    // handshake completes — or speech in that gap is never recorded.
    await vi.waitFor(() => expect(captureCreated).toBe(true));
    expect(readySeen).toBe(false);
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.onopen?.({});
    await vi.waitFor(() => expect(socket.sent.length).toBeGreaterThan(0));
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.started",
        request_id: requestId,
        engine: "local-window",
      }),
    });
    await startP;
    readySeen = true;
    await adapter.stop();
  });

  it("transmits pre-ready speech first (burst-at-t0 onset preserved)", async () => {
    const socket = fakeSocket();
    const holder: { frameCb: ((pcm: Int16Array) => void) | null } = {
      frameCb: null,
    };
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
      openSocket: () => socket,
      getUserMedia: async () => ({}) as MediaStream,
      createCapture: (_stream, onFrame): PcmCapture => {
        holder.frameCb = onFrame;
        return { start: () => undefined, stop: () => undefined };
      },
    });
    const startP = adapter.start({
      onPartial: () => undefined,
      onFinal: () => undefined,
      onError: () => undefined,
    });
    // Burst at t=0: the user speaks the instant they press Voice, while the
    // ticket/socket handshake is still in flight.
    await vi.waitFor(() => expect(holder.frameCb).not.toBeNull());
    holder.frameCb?.(new Int16Array(4800).fill(1200));
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.onopen?.({});
    await vi.waitFor(() => expect(socket.sent.length).toBeGreaterThan(0));
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.started",
        request_id: requestId,
        engine: "local-window",
      }),
    });
    await startP;
    const binary = socket.sent.find((s) => s instanceof ArrayBuffer);
    expect(binary).toBeInstanceOf(ArrayBuffer);
    const decoded = decodeBinaryFrame(binary as ArrayBuffer);
    expect(decoded.samples).toBeGreaterThanOrEqual(4800);
    await adapter.stop();
  });

  it("bounds the pre-ready ring (~1 s; oldest room tone yields, never newest)", async () => {
    const socket = fakeSocket();
    const holder: { frameCb: ((pcm: Int16Array) => void) | null } = {
      frameCb: null,
    };
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
      openSocket: () => socket,
      getUserMedia: async () => ({}) as MediaStream,
      createCapture: (_stream, onFrame): PcmCapture => {
        holder.frameCb = onFrame;
        return { start: () => undefined, stop: () => undefined };
      },
    });
    const startP = adapter.start({
      onPartial: () => undefined,
      onFinal: () => undefined,
      onError: () => undefined,
    });
    await vi.waitFor(() => expect(holder.frameCb).not.toBeNull());
    // ~3 s of speech while the handshake stalls: the ring keeps the newest.
    for (let i = 0; i < 10; i++) holder.frameCb?.(new Int16Array(4800).fill(1200));
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.onopen?.({});
    await vi.waitFor(() => expect(socket.sent.length).toBeGreaterThan(0));
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.started",
        request_id: requestId,
        engine: "local-window",
      }),
    });
    await startP;
    let total = 0;
    for (const s of socket.sent) {
      if (s instanceof ArrayBuffer) total += decodeBinaryFrame(s).samples;
    }
    // ~1 s ring (16000 samples) + the in-flight frame, never the full 3 s.
    expect(total).toBeLessThanOrEqual(16000 + 4800);
    expect(total).toBeGreaterThanOrEqual(16000 - 4800);
    await adapter.stop();
  });
});

describe("resampleLinear", () => {
  it("resamples 48 kHz to 16 kHz length correctly", () => {
    const input = new Float32Array(4800);
    expect(resampleLinear(input, 48000, 16000).length).toBe(1600);
    expect(resampleLinear(input, 16000, 16000)).toBe(input);
  });
});

describe("LocalStreamingSTTAdapter teardown (stuck-listener fix lane)", () => {
  function events() {
    const partials: string[] = [];
    const finals: string[] = [];
    const errors: Array<[string, string]> = [];
    const engines: string[] = [];
    return {
      partials,
      finals,
      errors,
      engines,
      sink: {
        onPartial: (t: string) => partials.push(t),
        onFinal: (t: string) => finals.push(t),
        onError: (c: string, d: string) => errors.push([c, d]),
        onEngine: (l: string) => engines.push(l),
      },
    };
  }

  it("releases capture when the ticket mint fails (no leaked capture)", async () => {
    let stopped = false;
    const socket = fakeSocket();
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => {
        throw new Error("no ticket");
      },
      openSocket: () => socket,
      getUserMedia: async () => ({}) as MediaStream,
      createCapture: () => ({
        start: () => undefined,
        stop: () => {
          stopped = true;
        },
      }),
    });
    const ev = events();
    await expect(adapter.start(ev.sink)).rejects.toMatchObject({
      code: "VOICE_STT_UNAVAILABLE",
    });
    // The onset capture was already opened on the button-press edge: a
    // refused backend must stop it, not just the mic tracks.
    expect(stopped).toBe(true);
  });

  it("releases capture when the socket drops mid-turn (no stuck mic)", async () => {
    const socket = fakeSocket();
    let stopped = false;
    const holder: { frameCb: ((pcm: Int16Array) => void) | null } = {
      frameCb: null,
    };
    const adapter = new LocalStreamingSTTAdapter({
      getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
      openSocket: () => socket,
      getUserMedia: async () => ({}) as MediaStream,
      createCapture: (_stream, onFrame): PcmCapture => {
        holder.frameCb = onFrame;
        return {
          start: () => undefined,
          stop: () => {
            stopped = true;
          },
        };
      },
    });
    const ev = events();
    const startP = adapter.start(ev.sink);
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.onopen?.({});
    await vi.waitFor(() => expect(socket.sent.length).toBeGreaterThan(0));
    const requestId = (
      JSON.parse(String(socket.sent[0])) as { request_id: string }
    ).request_id;
    socket.onmessage?.({
      data: JSON.stringify({
        type: "stt.started",
        request_id: requestId,
        engine: "local-window",
      }),
    });
    await startP;
    expect(stopped).toBe(false);
    socket.onclose?.({ code: 1006, reason: "abnormal" });
    expect(
      ev.errors.some(([c]) => c === "VOICE_STT_DISCONNECTED"),
    ).toBe(true);
    expect(stopped).toBe(true);
    expect(socket.closed).toBe(true);
  });

  it("auto-releases a quiet backend after 60 s with no server events", async () => {
    vi.useFakeTimers();
    try {
      const socket = fakeSocket();
      let stopped = false;
      const adapter = new LocalStreamingSTTAdapter({
        getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
        openSocket: () => socket,
        getUserMedia: async () => ({}) as MediaStream,
        createCapture: () => ({
          start: () => undefined,
          stop: () => {
            stopped = true;
          },
        }),
      });
      const ev = events();
      const startP = adapter.start(ev.sink);
      // Let the button-press-edge capture + socket handshake run to the
      // point where the open handler is assigned (fake timers: the 4 s
      // handshake timeouts are pending but untouched by a 0 ms advance).
      await vi.advanceTimersByTimeAsync(0);
      socket.onopen?.({});
      await vi.advanceTimersByTimeAsync(0);
      const requestId = (
        JSON.parse(String(socket.sent[0])) as { request_id: string }
      ).request_id;
      socket.onmessage?.({
        data: JSON.stringify({
          type: "stt.started",
          request_id: requestId,
          engine: "local-window",
        }),
      });
      await startP;
      expect(socket.closed).toBe(false);
      expect(ev.errors).toEqual([]);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(
        ev.errors.some(([c]) => c === "VOICE_STT_NO_PROGRESS"),
      ).toBe(true);
      expect(socket.closed).toBe(true);
      expect(stopped).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("server partials reset the no-progress watchdog", async () => {
    vi.useFakeTimers();
    try {
      const socket = fakeSocket();
      const adapter = new LocalStreamingSTTAdapter({
        getWsUrl: async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t",
        openSocket: () => socket,
        getUserMedia: async () => ({}) as MediaStream,
        createCapture: () => ({
          start: () => undefined,
          stop: () => undefined,
        }),
      });
      const ev = events();
      const startP = adapter.start(ev.sink);
      await vi.advanceTimersByTimeAsync(0);
      socket.onopen?.({});
      await vi.advanceTimersByTimeAsync(0);
      const requestId = (
        JSON.parse(String(socket.sent[0])) as { request_id: string }
      ).request_id;
      const started = JSON.stringify({
        type: "stt.started",
        request_id: requestId,
        engine: "local-window",
      });
      socket.onmessage?.({ data: started });
      await startP;
      const partial = (text: string) =>
        socket.onmessage?.({
          data: JSON.stringify({
            type: "stt.partial",
            request_id: requestId,
            text,
          }),
        });
      // 59 s quiet, one partial (progress), 59 s more quiet: still alive
      // (118 s wall, 59 s since progress).
      await vi.advanceTimersByTimeAsync(59_000);
      partial("he");
      await vi.advanceTimersByTimeAsync(59_000);
      expect(ev.errors).toEqual([]);
      // 60 s since the last server event: auto-release fires.
      await vi.advanceTimersByTimeAsync(1_000);
      expect(
        ev.errors.some(([c]) => c === "VOICE_STT_NO_PROGRESS"),
      ).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
