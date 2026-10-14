/* F12 — the staged-command model: stage, settle, idempotency, e-stop.
 *
 * C4 IS ABSOLUTE HERE: this module NEVER fetches, never POSTs, never resolves
 * its own confirmation. It builds the payload a caller would hand to the
 * existing authority, remembers what it staged, and applies ONLY what the
 * authority reads back. A staged command is a REQUEST; the receipt is the only
 * thing that turns it into a fact.
 *
 * THE DUPLICATE-CLICK BUG THIS PREVENTS
 * -------------------------------------
 * A button that stages on every press is a button that settles twice on a
 * double-press: two identical ops, two journal entries, two ledger rows. The
 * guard is an IDEMPOTENCY KEY derived from the REQUEST IDENTITY (the op, its
 * target and the revision it was aimed at). A second submit with the same key
 * is a NO-OP that reports the receipt the first one produced — it does not mint
 * a second key and it does not stage again.
 *
 * WHY THE KEY IS DERIVED, NOT RANDOM
 * ----------------------------------
 * A random key is unique per call, so a retry looks like a fresh request and the
 * guard is decorative. The key must be a pure function of what is being asked,
 * so the same ask has the same key however many times it arrives.
 */

/** A mutation as the UI may *propose* it. It is never the mutation itself. */
export interface StagedOp {
  /** The action id the authority recognises, e.g. `office.floor.update`. */
  action: string;
  /** The entity it targets. Stable ids only. */
  targetKind: string;
  targetId: string | number;
  /** The ordered arguments. Kept as scalars so the key is stable. */
  args: Record<string, string | number | boolean | null>;
}

/** The revision a stage was aimed at. `null` means "the caller did not pin one". */
export type Revision = number | null;

export interface StagedCommand {
  /** The key the caller sends so a retry is recognised as the same request. */
  idempotencyKey: string;
  identity: string;
  op: StagedOp;
  /** The revision this stage expected. A mismatch is a conflict, not a no-op. */
  revision: Revision;
  /** The authority's stage id, once it answered. Empty before that. */
  stageId: string;
  state: "staged" | "settled" | "refused" | "conflict";
  /** The authority's own words on refusal, never invented. */
  reason: string;
}

export interface CommandState {
  /** Commands by idempotency key. The map IS the dedupe. */
  byKey: Record<string, StagedCommand>;
  /** Order of first submission, for a stable render. */
  order: string[];
  /** Latched emergency stop. While true, no stage is attempted. */
  estop: boolean;
}

export function initialCommandState(): CommandState {
  return { byKey: {}, order: [], estop: false };
}

/**
 * The canonical request identity. Two asks that mean the same thing produce the
 * same string; a changed target or argument produces a different one.
 *
 * Arguments are sorted so `{a,b}` and `{b,a}` are one identity — object key
 * order is an accident of construction and must not fork the key.
 */
export function requestIdentity(op: StagedOp, revision: Revision): string {
  const args = Object.keys(op.args).sort()
    .map((k) => `${k}=${formatArg(op.args[k])}`)
    .join("&");
  return [
    op.action,
    op.targetKind,
    String(op.targetId),
    args,
    revision === null ? "rev=?" : `rev=${revision}`,
  ].join("|");
}

function formatArg(v: string | number | boolean | null): string {
  if (v === null) return "null";
  return String(v);
}

/**
 * The idempotency key. A pure function of the identity — NOT random, so a
 * duplicate click rebuilds the SAME key and is recognised.
 */
export function idempotencyKey(op: StagedOp, revision: Revision): string {
  const id = requestIdentity(op, revision);
  return fnv1a(id);
}

function fnv1a(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    // 32-bit FNV-1a multiply, kept in range with `>>> 0`.
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `idem-${h.toString(16).padStart(8, "0")}`;
}

/** The result of asking to stage. `duplicate` means NOTHING was staged again. */
export type StageResult =
  | { kind: "staged"; state: CommandState; command: StagedCommand }
  | { kind: "duplicate"; state: CommandState; command: StagedCommand }
  | { kind: "estop"; state: CommandState; reason: string };

/**
 * Record a stage attempt. This is the ONLY way a command enters the model, and
 * it is the dedupe: a key already present is reported, never re-staged.
 *
 * NOTE the ordering: e-stop is checked FIRST. A latched stop refuses regardless
 * of what the panel has open (50-BACKEND-CONTRACT: "E-stop must refuse
 * regardless of modal state").
 */
export function stageCommand(
  state: CommandState,
  op: StagedOp,
  revision: Revision = null,
): StageResult {
  if (state.estop) {
    return {
      kind: "estop",
      state,
      reason: "emergency stop is latched; no action is staged",
    };
  }
  const key = idempotencyKey(op, revision);
  const existing = state.byKey[key];
  if (existing) {
    // THE DUPLICATE CLICK. Same key => the same request. Report it; do not
    // stage a second one.
    return { kind: "duplicate", state, command: existing };
  }
  const command: StagedCommand = {
    idempotencyKey: key,
    identity: requestIdentity(op, revision),
    op,
    revision,
    stageId: "",
    state: "staged",
    reason: "",
  };
  return {
    kind: "staged",
    state: {
      ...state,
      byKey: { ...state.byKey, [key]: command },
      order: [...state.order, key],
    },
    command,
  };
}

/** The authority's answer to a stage: a real stage id, or a refusal. */
export interface StageAck {
  idempotencyKey: string;
  /** The authority's stage id. Empty on refusal. Never client-generated. */
  stageId: string;
  ok: boolean;
  reason?: string;
  /** The revision the authority was on. A mismatch against our aim is a conflict. */
  revision?: number;
}

/**
 * Apply the authority's stage answer. A conflict (the authority moved on) does
 * NOT silently succeed: it marks the command conflicted so the UI can ask for a
 * fresh decision rather than settling against a stale aim.
 */
export function applyStageAck(
  state: CommandState,
  ack: StageAck,
): CommandState {
  const cur = state.byKey[ack.idempotencyKey];
  if (!cur) return state;
  if (!ack.ok) {
    return replace(state, {
      ...cur, state: "refused", reason: ack.reason ?? "the action was refused",
    });
  }
  if (cur.revision !== null && ack.revision !== undefined
      && ack.revision !== cur.revision) {
    return replace(state, {
      ...cur, state: "conflict", stageId: ack.stageId,
      reason: `the target moved to revision ${ack.revision}; ` +
              `this stage aimed at ${cur.revision}`,
    });
  }
  return replace(state, { ...cur, stageId: ack.stageId });
}

/** The authoritative read-back of a settle. The ONLY source of "it landed". */
export interface SettleReceipt {
  idempotencyKey: string;
  /** Whether the authority says the op actually ran. */
  applied: boolean;
  /** The entity state READ BACK from the authority after the settle. */
  readback: Record<string, unknown> | null;
  reason?: string;
}

/**
 * Apply a settle receipt. The command becomes `settled` ONLY when the authority
 * says it applied AND the read-back is non-empty — an optimistic panel would
 * mark it settled on the request alone, which is the C4 lie.
 */
export function applySettleReceipt(
  state: CommandState,
  receipt: SettleReceipt,
): CommandState {
  const cur = state.byKey[receipt.idempotencyKey];
  if (!cur) return state;
  if (!receipt.applied || receipt.readback === null) {
    return replace(state, {
      ...cur, state: "refused",
      reason: receipt.reason ?? "the settle did not apply",
    });
  }
  return replace(state, { ...cur, state: "settled", reason: "" });
}

function replace(state: CommandState, command: StagedCommand): CommandState {
  return {
    ...state,
    byKey: { ...state.byKey, [command.idempotencyKey]: command },
  };
}

/** Latch / release the emergency stop. Latching refuses every future stage. */
export function setEstop(state: CommandState, engaged: boolean): CommandState {
  return { ...state, estop: !!engaged };
}

/**
 * The payload a caller hands to the EXISTING authority. Note it names the
 * action, the expected revision and the idempotency key — the three things
 * 50-BACKEND-CONTRACT requires — and nothing about how to reach the network.
 * This module does not fetch; the caller's adapter does.
 */
export function stageRequest(
  op: StagedOp,
  revision: Revision,
): { action: string; idempotencyKey: string; revision: Revision;
     target: { kind: string; id: string | number }; args: StagedOp["args"] } {
  return {
    action: op.action,
    idempotencyKey: idempotencyKey(op, revision),
    revision,
    target: { kind: op.targetKind, id: op.targetId },
    args: scopeArgs(op.args),
  };
}

/** Only scalars leave the client. A nested object is a builder's mistake. */
function scopeArgs(
  args: StagedOp["args"],
): Record<string, string | number | boolean | null> {
  const out: Record<string, string | number | boolean | null> = {};
  for (const k of Object.keys(args).sort()) out[k] = args[k];
  return out;
}

/** Every command, in first-submission order, for a stable render. */
export function orderedCommands(state: CommandState): StagedCommand[] {
  return state.order.map((k) => state.byKey[k]).filter(Boolean);
}
