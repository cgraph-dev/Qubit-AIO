import { describe, expect, it } from "vitest";
import { ar } from "./ar";
import { en } from "./en";

describe("Arabic navigation coverage", () => {
  it("translates every top-level sidebar label used by the shell", () => {
    for (const key of [
      "files",
      "mcp",
      "channels",
      "webhooks",
      "pairing",
      "system",
      "agents",
      "qubitChat",
      "voice",
      "tasksScheduling",
      "memory",
      "workspace",
      "admin",
      "qubitTv",
      "canvas",
      "office",
      "quest",
      "voiceChat",
    ]) {
      expect(ar.app.nav[key], key).toBeTruthy();
      // Technical names (MCP) intentionally stay identical.
      if (!["mcp", "qubitTv"].includes(key)) {
        expect(ar.app.nav[key]).not.toBe(en.app.nav[key]);
      }
    }
  });

  it("translates every sidebar section heading", () => {
    for (const key of ["primary", "work", "voice", "agents", "connect", "system", "more"]) {
      expect(ar.app.sections?.[key], key).toBeTruthy();
      expect(ar.app.sections?.[key]).not.toBe(en.app.sections?.[key]);
    }
  });

  it("translates the skip link", () => {
    expect(ar.common.skipToContent).toBeTruthy();
    expect(ar.common.skipToContent).not.toBe(en.common.skipToContent);
  });
});
