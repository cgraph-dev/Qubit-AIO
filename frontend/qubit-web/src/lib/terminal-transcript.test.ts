import { describe, expect, it } from "vitest";
import {
  TerminalTranscript,
  announcementFor,
  stripTerminalControl,
} from "./terminal-transcript";

describe("stripTerminalControl", () => {
  it("removes CSI colour/cursor sequences but keeps the words", () => {
    expect(stripTerminalControl("\x1b[31mHello\x1b[0m \x1b[2Kworld")).toBe(
      "Hello world",
    );
  });

  it("removes OSC title sequences", () => {
    expect(stripTerminalControl("\x1b]0;title\x07visible")).toBe("visible");
  });

  it("normalises carriage-return redraws to newlines", () => {
    expect(stripTerminalControl("one\r\ntwo\rthree")).toBe("one\ntwo\nthree");
  });

  it("drops remaining C0 control characters", () => {
    expect(stripTerminalControl("a\x07b\x08c")).toBe("abc");
  });
});

describe("TerminalTranscript", () => {
  it("accumulates cleaned chunks and keeps a bounded tail", () => {
    const transcript = new TerminalTranscript(8);
    transcript.append("\x1b[32mabc\x1b[0m");
    transcript.append("def");
    expect(transcript.text).toBe("abcdef");
    transcript.append("ghij");
    expect(transcript.text).toBe("cdefghij");
  });

  it("resets on a session switch", () => {
    const transcript = new TerminalTranscript();
    transcript.append("old session");
    transcript.reset();
    expect(transcript.text).toBe("");
  });

  it("ignores control-only chunks", () => {
    const transcript = new TerminalTranscript();
    transcript.append("\x1b[2J\x1b[H");
    expect(transcript.text).toBe("");
  });
});

describe("announcementFor", () => {
  it("announces only the new readable text", () => {
    expect(announcementFor("\x1b[36mready\x1b[0m\n")).toBe("ready");
  });

  it("collapses multi-line redraws and bounds long output", () => {
    expect(announcementFor("a\n\n b\tc")).toBe("a b c");
    expect(announcementFor("x".repeat(700)).endsWith("…")).toBe(true);
  });
});
