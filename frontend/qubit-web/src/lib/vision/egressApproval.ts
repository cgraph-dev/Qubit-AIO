/**
 * Vision egress approval — the PURE decision gate for sending an image to a
 * REMOTE (API/frontier) vision model.
 *
 * Owner ruling (wave 4/5): Qubit may offload vision work to an API, but the
 * user must APPROVE or DECLINE the upload in the UI BEFORE any image byte
 * leaves the machine. There is never silent egress.
 *
 * This module is deliberately pure (no React, no fetch, no DOM): it is the one
 * place the "is this send allowed?" question is answered, so the UI, a hook,
 * and a test all share the exact same rules. A caller MUST consult
 * {@link canSendVision} immediately before invoking the network path; when it
 * returns false, the caller performs NO request at all.
 *
 * The shape mirrors `voicebridge.vision.vision_target()` (the server's
 * authoritative report): the UI never guesses locality from the model name.
 */

/** Where the resolved vision profile would run. */
export type VisionLocality = "local" | "remote";

/** The server's `vision_target()` report (subset the UI needs). */
export interface VisionTarget {
  /** True when the resolved profile would send bytes off-machine. */
  remote: boolean;
  /** True when the target passes the frontier policy (enabled/allowlist/egress). */
  executable: boolean;
  /** The resolved model id (e.g. `qubit-brain-vision:latest`, `openai/gpt-4o`). */
  model: string;
  /** The resolved provider id (e.g. `ollama`, `openrouter`). */
  provider: string;
  /** True when egress consent is required for this profile. */
  egress_required: boolean;
  /** True when the frontier policy currently permits egress. */
  egress_allowed: boolean;
  /** True when the provider profile is enabled in policy. */
  policy_enabled: boolean;
  /** True when the provider is on the allowlist. */
  allowlisted: boolean;
  /** A short reason when the target is not executable (server-supplied). */
  reason?: string;
}

/** The user's answer to the approval popup. */
export type VisionApproval = "pending" | "approved" | "declined";

/** What the surface must do before it may send an image. */
export interface VisionEgressDecision {
  /** True when the UI must show the approve/decline popup before sending. */
  needsApproval: boolean;
  /** True when the target can never send (policy denies egress) — no popup. */
  blocked: boolean;
  /** Human-readable reason for {@link blocked} (empty when not blocked). */
  blockReason: string;
}

/** Locality of a target, derived from the single `remote` flag. */
export function visionLocality(target: VisionTarget | null | undefined): VisionLocality {
  return target && target.remote ? "remote" : "local";
}

/**
 * Decide whether the UI must ask for approval, and whether the target is
 * blocked outright.
 *
 * Rules:
 * - UNKNOWN target (null/undefined) → blocked: we cannot know whether the send
 *   egresses, so it fails closed rather than assuming local.
 * - LOCAL target → no approval, never blocked (no egress to approve).
 * - REMOTE + policy-denied (not executable) → blocked, no point showing a
 *   popup the user cannot act on; the send is a typed refusal.
 * - REMOTE + executable → needs approval.
 */
export function visionEgressDecision(
  target: VisionTarget | null | undefined,
): VisionEgressDecision {
  if (!target) {
    return {
      needsApproval: false,
      blocked: true,
      blockReason: "vision target is unknown — refusing to send without knowing where it goes",
    };
  }
  if (!target.remote) {
    return { needsApproval: false, blocked: false, blockReason: "" };
  }
  if (!target.executable) {
    const reason =
      (target.reason || "").trim() ||
      (target.egress_required && !target.egress_allowed
        ? "egress is denied by policy"
        : "the vision profile is not executable");
    return { needsApproval: false, blocked: true, blockReason: reason };
  }
  return { needsApproval: true, blocked: false, blockReason: "" };
}

/**
 * The single pre-send gate.
 *
 * Returns true ONLY when the send may proceed:
 * - LOCAL target → always true (no egress).
 * - REMOTE target → true only when the target is executable AND the user
 *   explicitly approved this send.
 *
 * A `false` result means the caller must perform NO network request. This is
 * the function a test asserts on for the "decline ⇒ zero outbound request"
 * guarantee.
 */
export function canSendVision(
  target: VisionTarget | null | undefined,
  approval: VisionApproval,
): boolean {
  const decision = visionEgressDecision(target);
  if (decision.blocked) return false;
  if (!decision.needsApproval) return true;
  return approval === "approved";
}

/** A stable one-line summary of what the popup is asking about. */
export function visionEgressSummary(target: VisionTarget | null | undefined): string {
  if (!target) return "Where this image is processed is unknown — the send is refused.";
  if (!target.remote) return "Runs locally — the image never leaves this machine.";
  const who = [target.provider, target.model].filter(Boolean).join("/");
  return `Sends the image to ${who || "the API"} — it will leave this machine.`;
}
