import { describe, expect, it } from "vitest";
import { browserAddress, browserSite } from "./browser-address";
import { isCompleteInstant, localDateTime, zonedInstant } from "./date-time";
import { dateLabel, relativeDate, timeLabel } from "./date-labels";
import { radii, sizes, spacing, type SurfaceColors } from "./tokens";
import { webSurfaceColors } from "./web-colors";

describe("browserAddress", () => {
  it("normalises a bare host to https", () => {
    expect(browserAddress("example.com")).toBe("https://example.com/");
  });
  it("keeps an explicit scheme", () => {
    expect(browserAddress("http://news.ycombinator.com")).toBe("http://news.ycombinator.com/");
  });
  it("rejects credentials and whitespace", () => {
    expect(() => browserAddress("https://user:pass@example.com")).toThrow();
    expect(() => browserAddress("exa mple.com")).toThrow();
  });
});

describe("browserSite", () => {
  it("drops www and returns the hostname", () => {
    expect(browserSite("https://www.example.com/x")).toBe("example.com");
  });
  it("falls back to Browser on invalid input", () => {
    expect(browserSite("not a url")).toBe("Browser");
  });
});

describe("date-time helpers", () => {
  it("formats a Z instant in a named zone", () => {
    expect(localDateTime("2026-01-15T12:34:00Z", "Europe/Bucharest")).toEqual({
      date: "2026-01-15",
      time: "14:34",
    });
  });
  it("round-trips a local wall clock to a UTC instant", () => {
    expect(zonedInstant("2026-01-15", "14:34", "Europe/Bucharest")).toBe("2026-01-15T12:34:00.000Z");
  });
  it("rejects a malformed date", () => {
    expect(() => zonedInstant("2026-1-1", "14:34", "UTC")).toThrow();
  });
  it("accepts only fully serialized instants", () => {
    expect(isCompleteInstant("2026-01-15T12:34:00Z")).toBe(true);
    expect(isCompleteInstant("2026-01-15")).toBe(false);
  });
});

describe("date labels", () => {
  it("uses the default short month/day format when no options are given", () => {
    // `dateLabel` only applies its short format when `options` is omitted;
    // an explicit options object is honoured verbatim (upstream behaviour).
    expect(dateLabel("2026-01-15T12:34:00Z")).toBe("Jan 15");
    expect(dateLabel("2026-01-15T12:34:00Z", { timeZone: "UTC" })).toBe("1/15/2026");
    expect(timeLabel("2026-01-15T12:34:00Z", "UTC")).toBe("12:34 PM");
  });
  it("returns the raw value for an invalid date", () => {
    expect(dateLabel("nope")).toBe("nope");
  });
  it("renders relative labels", () => {
    expect(relativeDate(new Date().toISOString())).toBe("Just now");
  });
});

describe("tokens", () => {
  it("exposes the ported upstream geometry", () => {
    expect(radii.card).toBe(23);
    expect(radii.button).toBe(24);
    expect(sizes.iconBox).toBe(42);
    expect(sizes.emptyIconBox).toBe(55);
    expect(sizes.iconButton).toBe(44);
    expect(sizes.inputMinHeight).toBe(45);
    expect(sizes.buttonMinHeight).toBe(42);
    expect(sizes.sheetMaxWidth).toBe(790);
    expect(sizes.sheetWideMaxWidth).toBe(1050);
    expect(spacing.cardPadding).toBe(20);
  });
  it("resolves web colours from Qubit theme vars, not hardcoded light hex", () => {
    const colors: SurfaceColors = webSurfaceColors;
    expect(colors.canvas).toContain("var(--background-base)");
    expect(colors.text).toContain("var(--color-foreground)");
    // None of the ported light hexes leaked into the Qubit token resolver.
    expect(Object.values(colors).join(" ")).not.toContain("#FCFCFC");
    expect(Object.values(colors).join(" ")).not.toContain("#11191C");
  });
});
