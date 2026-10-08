/**
 * @vitest-environment jsdom
 *
 * Contract test: a server `resync` sentinel must NOT blank the journal cursor.
 *
 * The SSE feed's contract (voicebridge/events/hub.py:resync_frame /
 * events-sse.ts header) is explicit: on a genuine cursor gap the client
 * resumes from the RETAINED `last_seq` so the journal replay fills the gap,
 * and does NOT restart cold. Blanking the cursor forced a whole-journal
 * replay — the "reconnect storm" the sibling ChatSidebar reader fixed.
 *
 * This pins the hook half: once an id'd frame has advanced the cursor, a
 * resync must leave the next `openEventsStream` call carrying that cursor
 * (a non-null `lastSeq`), never null.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const openSpy = vi.fn();

// A tiny controllable stream: we push chunks and the reader yields them once.
function makeStream(chunks: string[]) {
  let i = 0;
  return {
    getReader() {
      return {
        read: async () => {
          if (i < chunks.length) {
            const c = chunks[i++];
            // Deliberately yield in one read; the hook re-parses on each read.
            return { value: new TextEncoder().encode(c), done: false };
          }
          return { value: undefined, done: true };
        },
        cancel: async () => {},
      };
    },
  };
}

vi.mock("@/lib/events-sse", async (orig) => {
  const actual = await orig<typeof import("@/lib/events-sse")>();
  return {
    ...actual,
    openEventsStream: (req: { lastSeq: number | null }) => {
      openSpy(req.lastSeq);
      // First connect: deliver an id'd frame (advances cursor to 5) then a
      // resync sentinel. Second connect (the resync reconnect): end at once.
      const chunks =
        openSpy.mock.calls.length === 1
          ? [
              'id: 5\ndata: {"type":"CUSTOM","name":"todo.updated","value":{"todos":[],"revision":1}}\n\n',
              ": resync\nevent: resync\ndata: {}\n\n",
            ]
          : [];
      return Promise.resolve({
        ok: true,
        status: 200,
        authRequired: false,
        reader: makeStream(chunks).getReader(),
      });
    },
  };
});

import { useChatActivity } from "@/hooks/useChatActivity";

let container: HTMLDivElement;
let root: Root | null = null;

function Probe() {
  useChatActivity("sess-1", true);
  return null;
}

describe("useChatActivity resync cursor", () => {
  beforeEach(() => {
    openSpy.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    vi.clearAllTimers();
  });

  it("retains the journal cursor across a resync (never reconnects cold)", async () => {
    vi.useFakeTimers();
    await act(async () => {
      root = createRoot(container);
      root.render(<Probe />);
    });
    // Let the first connect resolve, parse the frame + resync, and schedule
    // the reconnect (RECONNECT_MS backoff).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    // First connect: cold (null). Reconnect after the resync: must be 5, the
    // retained cursor — a null here is the regression (cold whole-journal
    // replay) this test exists to catch.
    expect(openSpy.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(openSpy.mock.calls[0][0]).toBeNull();
    expect(openSpy.mock.calls[1][0]).toBe(5);
    vi.useRealTimers();
  });
});
