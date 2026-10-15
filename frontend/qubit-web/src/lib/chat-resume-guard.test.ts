import { describe, expect, it } from "vitest";

import {
  isResumeProbeMissing,
  resumeMissingMessage,
  resumeProbeState,
  shouldDialTerminalForResume,
  shouldShowResumeChecking,
} from "./chat-resume-guard";

describe("chat-resume-guard", () => {
  it("treats a missing ?resume as a legitimate fresh chat", () => {
    expect(resumeProbeState({ resumeId: "", verifiedId: null, missingId: null }))
      .toBe("absent");
    expect(shouldDialTerminalForResume("absent")).toBe(true);
  });

  it("does NOT dial the PTY while the resume id is still being verified", () => {
    // Regression: dialing before the probe resolves is what painted an empty
    // canvas (and, server-side, silently created a phantom session for a bogus
    // id via canonical_terminal_session's ensure()).
    const state = resumeProbeState({
      resumeId: "20261014_232207_991628",
      verifiedId: null,
      missingId: null,
    });
    expect(state).toBe("checking");
    expect(shouldDialTerminalForResume(state)).toBe(false);
    expect(shouldShowResumeChecking(state)).toBe(true);
  });

  it("dials once the id is verified to exist", () => {
    const state = resumeProbeState({
      resumeId: "20261014_232207_991628",
      verifiedId: "20261014_232207_991628",
      missingId: null,
    });
    expect(state).toBe("known");
    expect(shouldDialTerminalForResume(state)).toBe(true);
    expect(shouldShowResumeChecking(state)).toBe(false);
  });

  it("refuses to dial a resume id that does not exist", () => {
    const state = resumeProbeState({
      resumeId: "does_not_exist_9999999999",
      verifiedId: null,
      missingId: "does_not_exist_9999999999",
    });
    expect(state).toBe("missing");
    expect(shouldDialTerminalForResume(state)).toBe(false);
  });

  it("lets a newly picked id override a stale missing verdict", () => {
    // The operator moves to another row: the old 'missing' id must not stick.
    const state = resumeProbeState({
      resumeId: "session-b",
      verifiedId: null,
      missingId: "session-a",
    });
    expect(state).toBe("checking");
  });

  it("names the offending id in the operator copy", () => {
    const msg = resumeMissingMessage("does_not_exist_9999999999");
    expect(msg).toContain("does_not_exist_9999999999");
    expect(msg).toMatch(/session list|new chat/i);
  });

  it("truncates a pathological id rather than echoing it whole", () => {
    const id = "x".repeat(200);
    const msg = resumeMissingMessage(id);
    // The id itself is clamped; the message never carries the whole 200-char id.
    expect(msg).not.toContain(id);
    expect(msg).toContain("…");
    expect(msg).toContain("x".repeat(77));
  });

  it("recognizes a 404 probe failure as 'missing' and a 500 as 'real error'", () => {
    expect(isResumeProbeMissing(new Error("404 not found"))).toBe(true);
    expect(isResumeProbeMissing(new Error("GET /api/sessions/x -> 404"))).toBe(true);
    expect(isResumeProbeMissing(new Error("500 internal server error"))).toBe(false);
    expect(isResumeProbeMissing(new Error("network unreachable"))).toBe(false);
  });
});
