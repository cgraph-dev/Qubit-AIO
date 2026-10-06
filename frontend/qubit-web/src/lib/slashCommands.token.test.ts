/**
 * Contract tests for the composer token helpers.
 *
 * These exist because the previous implementation anchored the token regex to
 * the START of the whole input (`/^\/[A-Za-z0-9_-]*$/.test(input)`), so a `/`
 * typed after any other character never opened the slash palette — the exact
 * behaviour the owner reported against `/qubit-chat`. The `/chat` terminal
 * completer finds the token anywhere; these pin the same contract.
 */
import { describe, expect, it } from "vitest";
import { atTokenAt, slashTokenAt } from "@/lib/slashCommands";

describe("slashTokenAt", () => {
  it("finds a bare slash at the start", () => {
    expect(slashTokenAt("/")?.token).toBe("/");
  });

  it("finds a partial command at the start", () => {
    expect(slashTokenAt("/mod")?.token).toBe("/mod");
  });

  // The regression: this is what the owner reported as broken.
  it("finds a slash MID-SENTENCE (the reported bug)", () => {
    expect(slashTokenAt("please run /")?.token).toBe("/");
    expect(slashTokenAt("please run /mod")?.token).toBe("/mod");
  });

  it("finds a slash after a newline", () => {
    expect(slashTokenAt("line one\n/mod")?.token).toBe("/mod");
  });

  it("returns the token start offset", () => {
    const hit = slashTokenAt("please run /mod");
    expect(hit?.start).toBe("please run ".length);
  });

  it("uses the caret, not the end, when one is given", () => {
    // Caret sits right after "/mo" in "please run /model".
    const input = "please run /model";
    const caret = input.indexOf("/mo") + 3;
    expect(slashTokenAt(input, caret)?.token).toBe("/mo");
  });

  it("closes once an argument follows a space", () => {
    expect(slashTokenAt("/model arg")).toBeNull();
  });

  it("does not treat a URL as a command", () => {
    // No whitespace boundary before the slash => not a composer token.
    expect(slashTokenAt("http://example.com")).toBeNull();
  });

  it("returns null for plain text", () => {
    expect(slashTokenAt("hello world")).toBeNull();
  });

  it("returns null for empty input", () => {
    expect(slashTokenAt("")).toBeNull();
  });

  it("rejects a slash glued to the previous word", () => {
    expect(slashTokenAt("text/mod")).toBeNull();
  });
});

describe("atTokenAt", () => {
  it("finds a mention at the start", () => {
    expect(atTokenAt("@skills")?.token).toBe("@skills");
  });

  // Same start-anchored bug the slash palette had.
  it("finds a mention MID-SENTENCE", () => {
    expect(atTokenAt("check @skills")?.token).toBe("@skills");
    expect(atTokenAt("check @")?.token).toBe("@");
  });

  it("does not fire on an email address", () => {
    expect(atTokenAt("mail me at user@example.com")).toBeNull();
  });

  it("closes once an argument follows a space", () => {
    expect(atTokenAt("@skills list")).toBeNull();
  });
});
