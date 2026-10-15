/**
 * `/chat?resume=<id>` existence guard.
 *
 * The terminal route dials a PTY that RESUMES whatever id the URL names. The
 * server resolves an explicit non-empty id through
 * `terminal_bridge.canonical_terminal_session`, which **ensures** the id in the
 * canonical store — so a mistyped, deleted, or foreign id is silently CREATED
 * as a brand-new empty session instead of being reported. The terminal then
 * paints an empty canvas: the reported "blank /chat until I click a session"
 * symptom, with a phantom session left behind in the store as a side effect.
 *
 * The route must therefore verify the id against the read-only session probe
 * (`GET /agent/api/sessions/<id>`, 200 = exists, 404 = missing) BEFORE it
 * dials the PTY, and render an explicit "session not found" state when the id
 * does not resolve.
 *
 * Pure and DOM-free so the decision is unit-tested on its own; the page only
 * wires the fetch to these predicates.
 */

/** The three states a `?resume=<id>` can be in before the terminal dials. */
export type ResumeProbeState = "absent" | "checking" | "known" | "missing";

export const RESUME_MISSING_TITLE = "Session not found";

/** Names the id so the operator can spot the typo, and the two ways out. */
export function resumeMissingMessage(id: string): string {
  const shown = id.length > 80 ? `${id.slice(0, 77)}…` : id;
  return (
    `No session "${shown}" exists on this Qubit instance, so /chat cannot ` +
    `resume it. Check the link, open the session list to pick a real one, or ` +
    `start a new chat.`
  );
}

/** True when a body/resource id is usable as a resume target at all. */
export function isResumeProbeMissing(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err ?? "");
  return /\b404\b/.test(message) || /\bnot found\b/i.test(message);
}

/**
 * Decide whether the page may dial the PTY for `resumeId`.
 *
 * - no id           -> dial (a plain /chat is a legitimate fresh chat)
 * - still checking  -> do NOT dial yet; the empty terminal would paint first
 * - verified known  -> dial
 * - verified missing-> do NOT dial; render the not-found state instead
 */
export function shouldDialTerminalForResume(state: ResumeProbeState): boolean {
  return state === "absent" || state === "known";
}

/** The notice to show above the terminal while a resume id is being verified. */
export function shouldShowResumeChecking(state: ResumeProbeState): boolean {
  return state === "checking";
}

/**
 * Collapse the probe effects into one state. Kept separate from the fetch so
 * the ordering (a resolved id wins over a stale "checking") is explicit and
 * testable without a DOM.
 */
export function resumeProbeState(input: {
  resumeId: string;
  verifiedId: string | null;
  missingId: string | null;
}): ResumeProbeState {
  const id = input.resumeId.trim();
  if (!id) return "absent";
  if (input.missingId === id) return "missing";
  if (input.verifiedId === id) return "known";
  return "checking";
}
