/**
 * Typed policy-refusal parsing for the Qubit dashboard.
 *
 * The Qubit backend answers some dashboard actions with a structured 403
 * envelope instead of a 2xx:
 *
 *   {"ok": false, "state": "UNAVAILABLE", "code": "UPDATE_OPERATOR_OWNED",
 *    "detail": "In-place self-update from the dashboard is refused; upgrades
 *               are versioned, staged, and reversible.",
 *    "qubit_counterpart": "CLI `qubit product_*` / package + dashboard Setup page",
 *    "owner": "qubit-policy", "rollback": "read-only; nothing to roll back"}
 *
 * These are *expected* policy refusals, not failures: the dashboard must
 * render the reason and the Qubit counterpart rather than a raw
 * `"403: {json}"` error string. `fetchJSON` throws `Error("<status>: <body>")`
 * for every non-2xx, so the envelope is recovered from the message here.
 */

export interface PolicyRefusal {
  /** Stable typed code, e.g. `UPDATE_OPERATOR_OWNED` or `SECRET_NEVER_SERVED`. */
  code: string;
  /** Machine state from the envelope, e.g. `UNAVAILABLE` / `REFUSED`. */
  state: string;
  /** Operator-facing explanation of why the action is refused. */
  detail: string;
  /** The supported Qubit path for the same capability (CLI/package/page). */
  counterpart?: string;
  /** Policy owner that authored the refusal. */
  owner?: string;
}

/** Recover a typed policy-refusal envelope from a `fetchJSON` error, if any. */
export function parsePolicyRefusal(err: unknown): PolicyRefusal | null {
  const message = err instanceof Error ? err.message : String(err ?? "");
  const at = message.indexOf("{");
  if (at < 0) return null;
  try {
    const body = JSON.parse(message.slice(at)) as Record<string, unknown>;
    if (!body || typeof body !== "object") return null;
    const code = typeof body.code === "string" ? body.code : "";
    const state = typeof body.state === "string" ? body.state : "";
    const detail = typeof body.detail === "string" ? body.detail : "";
    if (!code && !detail) return null;
    return {
      code,
      state,
      detail,
      counterpart:
        typeof body.qubit_counterpart === "string"
          ? body.qubit_counterpart
          : undefined,
      owner: typeof body.owner === "string" ? body.owner : undefined,
    };
  } catch {
    // Not a JSON envelope (plain-text error) — not a typed refusal.
    return null;
  }
}

/** True when the error is the operator-owned dashboard self-update refusal. */
export function isUpdateOperatorOwnedRefusal(err: unknown): boolean {
  return parsePolicyRefusal(err)?.code === "UPDATE_OPERATOR_OWNED";
}

/** Human-readable guidance: the refusal detail plus the Qubit counterpart. */
export function policyRefusalMessage(refusal: PolicyRefusal): string {
  return [refusal.detail, refusal.counterpart]
    .filter((part): part is string => !!part)
    .join(" ");
}

/**
 * A typed policy refusal becomes its readable message (detail + counterpart);
 * any other error passes through as the caller's fallback. Dashboard actions
 * that hit a documented Qubit gate use this so the user sees the *reason*
 * instead of a raw `"501: {json}"` string.
 */
export function refusalOrError(error: unknown, fallback: string): string {
  const refusal = parsePolicyRefusal(error);
  return refusal ? policyRefusalMessage(refusal) : fallback;
}
