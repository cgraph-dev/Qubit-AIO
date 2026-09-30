import { describe, expect, it } from "vitest";

import { eventsStreamUrl, parseSseChunk } from "./events-sse";

describe("parseSseChunk", () => {
  it("parses id/event/data frames and leaves a partial tail buffered", () => {
    const input =
      "id: 1\ndata: {\"type\":\"RUN_STARTED\"}\n\n" +
      "id: 2\ndata: {\"type\":\"RUN_FINISHED\"}\n\n" +
      "id: 3\ndata: {\"partial\":";
    const parsed = parseSseChunk(input);
    expect(parsed.frames).toEqual([
      { id: 1, event: "message", data: '{"type":"RUN_STARTED"}' },
      { id: 2, event: "message", data: '{"type":"RUN_FINISHED"}' },
    ]);
    expect(parsed.rest).toBe('id: 3\ndata: {"partial":');
    expect(parsed.resync).toBe(false);
  });

  it("ignores heartbeats and retry-only blocks", () => {
    const parsed = parseSseChunk("retry: 3000\n\n: ping\n\n");
    expect(parsed.frames).toEqual([]);
    expect(parsed.rest).toBe("");
  });

  it("flags the resync sentinel", () => {
    const parsed = parseSseChunk(": resync\nevent: resync\ndata: {}\n\n");
    expect(parsed.resync).toBe(true);
    expect(parsed.frames[0].event).toBe("resync");
  });

  it("joins multiple data lines with newlines", () => {
    const parsed = parseSseChunk("data: a\ndata: b\n\n");
    expect(parsed.frames[0].data).toBe("a\nb");
  });
});

describe("eventsStreamUrl", () => {
  it("omits the cursor when unknown so the server replays the journal", () => {
    expect(eventsStreamUrl(null)).toBe("/api/events/stream");
  });

  it("appends last_seq for replay", () => {
    expect(eventsStreamUrl(42)).toBe("/api/events/stream?last_seq=42");
  });

  it("honors a server base path", () => {
    expect(eventsStreamUrl(1, "/dash")).toBe("/dash/api/events/stream?last_seq=1");
  });
});
