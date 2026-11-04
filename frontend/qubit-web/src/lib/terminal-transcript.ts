/* eslint-disable no-control-regex -- PTY streams legitimately contain control bytes. */
/**
 * Accessible text mirror for the xterm `/chat` surface.
 *
 * xterm paints characters into a canvas/WebGL raster; screen-reader users get
 * nothing from it. The PTY stream is still text, so we strip terminal control
 * sequences into a bounded transcript and announce only the newest chunk.
 */

export const TERMINAL_TRANSCRIPT_LIMIT = 8000;

const OSC_SEQUENCE = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
const DCS_SEQUENCE = /\x1b[PX^_][^\x1b]*(?:\x1b\\)/g;
const CSI_SEQUENCE = /\x1b\[[0-?]*[ -/]*[@-~]/g;
const ESCAPE_SEQUENCE = /\x1b[@-_]/g;
const CONTROL_CHARS = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g;

/** Convert a raw PTY chunk into readable plain text. */
export function stripTerminalControl(input: string): string {
  if (!input) return "";
  return input
    .replace(OSC_SEQUENCE, "")
    .replace(DCS_SEQUENCE, "")
    .replace(CSI_SEQUENCE, "")
    .replace(ESCAPE_SEQUENCE, "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(CONTROL_CHARS, "");
}

/**
 * Bounded rolling transcript. Resetting is explicit because a session switch
 * clears the visual terminal and must clear the spoken mirror too.
 */
export class TerminalTranscript {
  private value = "";
  private readonly maxChars: number;

  constructor(maxChars = TERMINAL_TRANSCRIPT_LIMIT) {
    this.maxChars = maxChars;
  }

  append(chunk: string): string {
    const clean = stripTerminalControl(chunk);
    if (!clean) return this.value;
    this.value = (this.value + clean).slice(-this.maxChars);
    return this.value;
  }

  reset(): void {
    this.value = "";
  }

  get text(): string {
    return this.value;
  }
}

/**
 * The part worth interrupting a screen reader for. Multi-line TUI redraws are
 * collapsed to single spaces so the live region stays a sentence, not a wall
 * of box-drawing whitespace.
 */
export function announcementFor(
  chunk: string,
  maxChars = 600,
): string {
  const clean = stripTerminalControl(chunk)
    .replace(/\n+/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
  if (clean.length <= maxChars) return clean;
  return `${clean.slice(0, maxChars - 1).trimEnd()}…`;
}
