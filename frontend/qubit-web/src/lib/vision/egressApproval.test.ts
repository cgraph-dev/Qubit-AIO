import { describe, expect, it } from "vitest";
import {
  canSendVision,
  visionEgressDecision,
  visionEgressSummary,
  visionLocality,
  type VisionTarget,
} from "./egressApproval";

/** A LOCAL target: no egress, no approval. */
function local(): VisionTarget {
  return {
    remote: false,
    executable: true,
    model: "qubit-brain-vision:latest",
    provider: "ollama",
    egress_required: false,
    egress_allowed: true,
    policy_enabled: true,
    allowlisted: true,
  };
}

/** A REMOTE target that passes policy → approval required. */
function remoteExecutable(): VisionTarget {
  return {
    remote: true,
    executable: true,
    model: "gpt-4o",
    provider: "openai",
    egress_required: true,
    egress_allowed: true,
    policy_enabled: true,
    allowlisted: true,
  };
}

/** A REMOTE target the frontier policy denies → blocked, no popup. */
function remoteDenied(): VisionTarget {
  return {
    remote: true,
    executable: false,
    model: "gpt-4o",
    provider: "openai",
    egress_required: true,
    egress_allowed: false,
    policy_enabled: true,
    allowlisted: true,
    reason: "egress is denied by policy",
  };
}

describe("visionLocality", () => {
  it("reports local for a local target and null", () => {
    expect(visionLocality(local())).toBe("local");
    expect(visionLocality(null)).toBe("local");
    expect(visionLocality(undefined)).toBe("local");
  });

  it("reports remote for a remote target", () => {
    expect(visionLocality(remoteExecutable())).toBe("remote");
  });
});

describe("visionEgressDecision", () => {
  it("never asks for approval on the local path", () => {
    expect(visionEgressDecision(local())).toEqual({
      needsApproval: false,
      blocked: false,
      blockReason: "",
    });
  });

  it("asks for approval on an executable remote target", () => {
    expect(visionEgressDecision(remoteExecutable())).toEqual({
      needsApproval: true,
      blocked: false,
      blockReason: "",
    });
  });

  it("blocks (no popup) when policy denies the remote target", () => {
    const d = visionEgressDecision(remoteDenied());
    expect(d.needsApproval).toBe(false);
    expect(d.blocked).toBe(true);
    expect(d.blockReason).toContain("denied");
  });

  it("treats a missing target as local (no approval)", () => {
    expect(visionEgressDecision(null).needsApproval).toBe(false);
  });
});

describe("canSendVision — the pre-send gate", () => {
  it("allows a local send regardless of approval state", () => {
    expect(canSendVision(local(), "pending")).toBe(true);
    expect(canSendVision(local(), "declined")).toBe(true);
    expect(canSendVision(local(), "approved")).toBe(true);
  });

  it("BLOCKS a remote send until the user explicitly approves", () => {
    // THE GUARANTEE: pending and declined must never allow a send.
    expect(canSendVision(remoteExecutable(), "pending")).toBe(false);
    expect(canSendVision(remoteExecutable(), "declined")).toBe(false);
    expect(canSendVision(remoteExecutable(), "approved")).toBe(true);
  });

  it("never allows a policy-denied remote target, even if 'approved'", () => {
    expect(canSendVision(remoteDenied(), "approved")).toBe(false);
    expect(canSendVision(remoteDenied(), "pending")).toBe(false);
  });

  it("never allows a remote send when the target is unknown", () => {
    expect(canSendVision(null, "approved")).toBe(false);
  });
});

describe("visionEgressSummary", () => {
  it("promises no egress for the local path", () => {
    expect(visionEgressSummary(local())).toMatch(/local/i);
  });

  it("names the provider/model for a remote target", () => {
    expect(visionEgressSummary(remoteExecutable())).toContain("openai/gpt-4o");
    expect(visionEgressSummary(remoteExecutable())).toMatch(/leave this machine/i);
  });

  it("flags an unknown target as refused", () => {
    expect(visionEgressSummary(null)).toMatch(/refused|unknown/i);
  });
});
