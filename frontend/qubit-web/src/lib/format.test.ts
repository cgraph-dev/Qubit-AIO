import { describe, expect, it } from "vitest";
import {
  formatDurationCompact,
  formatDurationLong,
  formatEpochTimestamp,
  formatProductVersion,
  formatTokenCount,
  toEpochMilliseconds,
} from "./format";

/**
 * Elapsed-time formatting for completed-work summaries. Compact for dense rows,
 * long for prose. A non-finite/absent value must read as the fallback so we
 * never print "0s" over unknown data.
 */
describe("formatDurationCompact", () => {
  it("formats sub-minute values in seconds", () => {
    expect(formatDurationCompact(42_000)).toBe("42s");
  });

  it("formats minutes and seconds", () => {
    expect(formatDurationCompact(65_000)).toBe("1m 5s");
  });

  it("formats hours and minutes", () => {
    expect(formatDurationCompact(3_900_000)).toBe("1h 5m");
  });

  it("formats days and hours", () => {
    expect(formatDurationCompact(90_000_000)).toBe("1d 1h");
  });

  it("shows at most two units", () => {
    // 1d 2h 3m 4s -> "1d 2h"
    expect(formatDurationCompact(93_784_000)).toBe("1d 2h");
  });

  it("returns undefined for unknown input so callers can show a dash", () => {
    expect(formatDurationCompact(undefined)).toBeUndefined();
    expect(formatDurationCompact(null)).toBeUndefined();
    expect(formatDurationCompact(-1)).toBeUndefined();
    expect(formatDurationCompact(Number.NaN)).toBeUndefined();
    expect(formatDurationCompact(Number.POSITIVE_INFINITY)).toBeUndefined();
  });

  it("renders a genuine zero as 0s", () => {
    expect(formatDurationCompact(0)).toBe("0s");
  });
});

describe("formatDurationLong", () => {
  it("phrases units in long form", () => {
    expect(formatDurationLong(3_900_000, "en")).toBe("1 hour, 5 minutes");
  });

  it("uses a single unit when only one applies", () => {
    expect(formatDurationLong(42_000, "en")).toBe("42 seconds");
  });

  it("falls back rather than printing a bogus duration", () => {
    expect(formatDurationLong(undefined)).toBe("—");
    expect(formatDurationLong(-5)).toBe("—");
    expect(formatDurationLong(Number.NaN)).toBe("—");
  });

  it("accepts a custom fallback", () => {
    expect(formatDurationLong(undefined, "en", "n/a")).toBe("n/a");
  });

  it("localizes phrasing to the given locale", () => {
    const de = formatDurationLong(3_900_000, "de");
    // German long units differ from English; assert only that it localized.
    expect(de).not.toBe(formatDurationLong(3_900_000, "en"));
    expect(de).toContain("1");
  });
});

describe("formatTokenCount (unchanged)", () => {
  it("still abbreviates thousands and millions", () => {
    expect(formatTokenCount(4096)).toBe("4.1K");
    expect(formatTokenCount(128_000)).toBe("128K");
    expect(formatTokenCount(1_000_000)).toBe("1M");
    expect(formatTokenCount(999)).toBe("999");
  });
});

describe("toEpochMilliseconds / formatEpochTimestamp", () => {
  it("treats 10-digit epoch values as seconds", () => {
    expect(toEpochMilliseconds(1_700_000_000)).toBe(1_700_000_000_000);
    expect(toEpochMilliseconds("1700000000")).toBe(1_700_000_000_000);
  });

  it("leaves millisecond values alone", () => {
    expect(toEpochMilliseconds(1_700_000_000_000)).toBe(1_700_000_000_000);
    expect(toEpochMilliseconds("1700000000000")).toBe(1_700_000_000_000);
  });

  it("parses ISO timestamps", () => {
    expect(toEpochMilliseconds("2026-10-24T10:00:00Z")).toBe(
      Date.parse("2026-10-24T10:00:00Z"),
    );
  });

  it("returns undefined for absent/invalid input", () => {
    expect(toEpochMilliseconds(null)).toBeUndefined();
    expect(toEpochMilliseconds(undefined)).toBeUndefined();
    expect(toEpochMilliseconds("")).toBeUndefined();
    expect(toEpochMilliseconds("not-a-date")).toBeUndefined();
    expect(toEpochMilliseconds(Number.NaN)).toBeUndefined();
  });

  it("formats seconds-valued timestamps in the requested locale", () => {
    const formatted = formatEpochTimestamp(1_700_000_000, "en-US");
    expect(formatted).toBe(new Date(1_700_000_000_000).toLocaleString("en-US"));
    expect(formatted).not.toContain("1970");
  });
});

describe("formatProductVersion", () => {
  it("never doubles the product prefix or the v", () => {
    expect(formatProductVersion("qubit-0.0.1")).toBe("v0.0.1");
    expect(formatProductVersion("qubit-1.0.0")).toBe("v1.0.0");
    expect(formatProductVersion("0.0.1")).toBe("v0.0.1");
    expect(formatProductVersion("v0.0.1")).toBe("v0.0.1");
  });

  it("passes non-version strings through unchanged", () => {
    expect(formatProductVersion("unknown")).toBe("unknown");
  });

  it("renders an em dash for absent or empty values", () => {
    expect(formatProductVersion(undefined)).toBe("—");
    expect(formatProductVersion(null)).toBe("—");
    expect(formatProductVersion("   ")).toBe("—");
  });
});
