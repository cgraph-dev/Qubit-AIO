/* F12 — the boardroom: one real CEO request, answered through the authority.
 *
 * THE SHAPE OF AN OWNER REQUEST (50-BACKEND-CONTRACT `OwnerRequest`):
 * a stable id, a class, who is asking, the affected work/entities, the
 * evidence, the OPTIONS and a RECOMMENDATION, the artifact, the deadline, the
 * current state/revision, and the allowed response actions.
 *
 * THE REQUEST HERE IS BUILT FROM REAL PAYLOAD STATE, NOT AUTHORED FICTION. The
 * boardroom's standing request is a SURVIVAL question, and its options come
 * from the fold the tower actually carries:
 *
 *   * the org's runway (`hud.runway_days` + `hud.runway_is_derived`) and the
 *     survival floor (`hud.survival.floor_days`) decide whether there is even a
 *     question;
 *   * the burn (`hud.burn_cents_per_day`) and the balance (net) are the
 *     affected work — the numbers a decision would move;
 *   * the evidence is the ledger fold named by `hud.pnl_source`.
 *
 * When the payload does not carry what a request needs (no runway, an empty
 * office), the boardroom says there is NO open request rather than inventing an
 * agenda. An agenda with no behind it is the theatre the product forbids.
 *
 * ANSWERING IS A STAGED OP (C4). `option`/`recommendation` are data the panel
 * renders; pressing one INITIATES a stage through `commands.ts` and shows the
 * confirmation the authority returns. The panel never POSTs to a store route,
 * never shows optimistic state, and never resolves its own confirmation.
 */

import type { OfficeTowerPayload } from "../types";
import { money, runwayView, type RunwayView } from "./money";
import {
  type CommandState, type StageAck, type SettleReceipt, type StagedOp,
  applySettleReceipt, applyStageAck, stageCommand, stageRequest,
} from "./commands";

/** One option the owner may choose. `id` is what the authority settles on. */
export interface RequestOption {
  id: string;
  label: string;
  /** What choosing this would do. Plain language. */
  effect: string;
  /** The recommendation marker. At most one option may carry it. */
  recommended: boolean;
}

/** An artifact the request attaches: named, with a reference the reader can open. */
export interface RequestArtifact {
  kind: string;
  /** A stable reference. Never a fabricated path. */
  ref: string;
  label: string;
}

/** A work/entity the decision would move. */
export interface AffectedWork {
  kind: string;
  id: string | number;
  label: string;
  /** The number that would change, already formatted from integer cents. */
  value: string;
}

export interface OwnerRequest {
  /** Stable id. Derived from the question + the revision it was raised at. */
  id: string;
  /** The class of ask. `blocking` must never be de-noised away. */
  requestClass: "blocking" | "review" | "advisory";
  urgency: "blocking" | "today" | "normal";
  /** Who is asking. The CEO seat, by slug, when the payload has one. */
  askingAgent: string;
  askingAgentId: number | null;
  summary: string;
  /** The options. Always at least two, always exactly one recommended. */
  options: RequestOption[];
  /** The recommendation, spelled out in a sentence beside the option. */
  recommendation: string;
  artifact: RequestArtifact | null;
  affected: AffectedWork[];
  evidence: string;
  /** The revision this request was raised against. */
  revision: number;
  /** What the owner may do. `refuse` is always allowed (a typed refusal). */
  actions: Array<"answer" | "refuse">;
}

/** The CEO seat in the payload, if it has one. */
function ceoSeat(payload: OfficeTowerPayload | null):
  { slug: string; id: number } | null {
  const chars = payload?.characters ?? [];
  const ceo = chars.find((c) => c.seat_type === "ceo");
  return ceo ? { slug: ceo.slug, id: ceo.agent_id } : null;
}

/**
 * The boardroom's open request, or null when the payload raises none.
 *
 * THE SURVIVAL QUESTION, CONSTRUCTED HONESTLY: it exists only when the runway is
 * DERIVED and BELOW the survival floor. A payload with no derived runway has no
 * survival question — it has an unknown, and asking the owner to decide on an
 * unknown would be asking them to decide on nothing.
 */
export function openRequest(
  payload: OfficeTowerPayload | null,
  revision: number | null = null,
): OwnerRequest | null {
  if (!payload || !payload.hud) return null;
  const h = payload.hud;
  const runway: RunwayView = runwayView(h.runway_days, h.runway_is_derived === true);
  const floorDays = Number(h.survival?.floor_days ?? NaN);
  // No derived runway => no survival question, full stop.
  if (runway.state !== "known" || runway.days === null
      || !Number.isFinite(floorDays)) {
    return null;
  }
  // Above the floor => no question either. A boardroom that always has an ask
  // is a boardroom that is never actually being consulted.
  if (runway.days >= floorDays) return null;

  const seat = ceoSeat(payload);
  const net = money(h.net_cents);
  const burn = money(Number(h.burn_cents_per_day)).text;
  const rev = Number(revision ?? payload.now ?? 0);

  const options: RequestOption[] = [
    {
      id: "cut_burn",
      label: "Cut burn to the survival floor",
      effect: "Trim metered inference so the runway clears the floor.",
      recommended: true,
    },
    {
      id: "hold",
      label: "Hold and keep working",
      effect: "Do nothing; accept the shorter runway.",
      recommended: false,
    },
    {
      id: "raise",
      label: "Raise the budget",
      effect: "Increase the daily budget to extend the runway.",
      recommended: false,
    },
  ];

  return {
    id: `boardroom:runway:${Math.round(runway.days)}`,
    requestClass: "advisory",
    urgency: "today",
    askingAgent: seat ? seat.slug : "the board",
    askingAgentId: seat ? seat.id : null,
    summary: `Runway is ${Math.round(runway.days)} days, below the ` +
             `${Math.round(floorDays)}-day survival floor ` +
             `(net ${net.text}, burn ${burn}).`,
    options,
    recommendation:
      `Recommended: ${options[0].label} — it is the smallest change that puts ` +
      `the runway back over the floor without stopping work.`,
    artifact: {
      kind: "pnl",
      ref: String(h.pnl_source ?? "office_v_org_pnl"),
      label: "The organisation P&L fold this question is computed from",
    },
    affected: [
      { kind: "metric", id: "runway_days", label: "Runway",
        value: `${Math.round(runway.days)} days` },
      { kind: "metric", id: "net_cents", label: "Net", value: net.text },
      { kind: "metric", id: "burn_cents_per_day", label: "Burn", value: burn },
      { kind: "metric", id: "survival.floor_days", label: "Survival floor",
        value: `${Math.round(floorDays)} days` },
    ],
    evidence: `hud fold: ${String(h.pnl_source ?? "")}`,
    revision: rev,
    actions: ["answer", "refuse"],
  };
}

/**
 * A request that needs a human is never de-noised away. This is the classifier
 * the boardroom shares with the notification discipline (C25): a blocking ask
 * survives any collapsing; only advisory noise may fold.
 */
export function requestNeedsHuman(req: OwnerRequest | null): boolean {
  if (!req) return false;
  return req.requestClass === "blocking" || req.urgency === "blocking";
}

// ---------------------------------------------------------------------------
// Answering: the stage/settle binding. Delegates to `commands.ts`; no fetch.
// ---------------------------------------------------------------------------
export interface AnswerInput {
  request: OwnerRequest;
  option: RequestOption;
  /** Confirm = answer; the panel never auto-answers. */
  decision: "answer" | "refuse";
}

/** The staged op an answer becomes. `action` names the canonical authority. */
export function answerOp(input: AnswerInput): StagedOp {
  return {
    action: "office.request.answer",
    targetKind: "owner_request",
    targetId: input.request.id,
    args: {
      option: input.option.id,
      decision: input.decision,
    },
  };
}

export interface AnswerResult {
  state: CommandState;
  request: ReturnType<typeof stageRequest>;
  duplicate: boolean;
  reason: string;
}

/**
 * Stage an answer. A duplicate click (same request, same option, same revision)
 * produces the SAME idempotency key and NO second stage.
 */
export function stageAnswer(
  state: CommandState,
  input: AnswerInput,
): AnswerResult {
  const op = answerOp(input);
  const res = stageCommand(state, op, input.request.revision);
  if (res.kind === "duplicate") {
    return {
      state: res.state, request: stageRequest(op, input.request.revision),
      duplicate: true,
      reason: `already staged as ${res.command.idempotencyKey}`,
    };
  }
  if (res.kind === "estop") {
    return {
      state: res.state, request: stageRequest(op, input.request.revision),
      duplicate: false, reason: res.reason,
    };
  }
  return {
    state: res.state, request: stageRequest(op, input.request.revision),
    duplicate: false, reason: "",
  };
}

export function ackAnswer(state: CommandState, ack: StageAck): CommandState {
  return applyStageAck(state, ack);
}

export function settleAnswer(
  state: CommandState,
  receipt: SettleReceipt,
): CommandState {
  return applySettleReceipt(state, receipt);
}

/**
 * Read the answer back against the request. This is the "read back the exact
 * target" step: the caller hands the authoritative read-back, and this says
 * whether the request it answered is the one that changed.
 */
export function verifyReadback(
  request: OwnerRequest,
  readback: Record<string, unknown> | null,
): { ok: boolean; reason: string } {
  if (readback === null) {
    return { ok: false, reason: "no read-back was returned" };
  }
  const id = readback.id ?? readback.request_id;
  if (String(id) !== request.id) {
    return {
      ok: false,
      reason: `the read-back is for ${String(id)}, not ${request.id}`,
    };
  }
  return { ok: true, reason: "" };
}

// ---------------------------------------------------------------------------
// Render — uses the HOST's React.
// ---------------------------------------------------------------------------
type ReactLike = { createElement: (...args: unknown[]) => unknown };

export interface BoardroomPanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
  revision?: number | null;
  commands?: CommandState;
  /** Called when the owner picks an option. The panel does not fetch itself. */
  onAnswer?: (input: AnswerInput) => void;
}

export function renderBoardroomPanel(props: BoardroomPanelProps): unknown {
  const { R, payload } = props;
  const req = openRequest(payload, props.revision ?? null);

  if (!req) {
    // NO INVENTED AGENDA. When the payload raises no request, the boardroom
    // says so plainly.
    return R.createElement("section", {
      "data-testid": "office-boardroom-panel",
      "data-request": "none",
      role: "region", "aria-label": "Boardroom",
    }, R.createElement("div", {
      "data-testid": "boardroom-no-request",
    }, "No open request. The runway is not below the survival floor, or it is " +
       "not derived — either way there is nothing to decide."));
  }

  const optionRows = req.options.map((o) => R.createElement("button", {
    key: o.id, type: "button",
    "data-testid": `boardroom-option-${o.id}`,
    "data-recommended": String(o.recommended),
    className: "office-game__btn",
    onClick: () => props.onAnswer?.({ request: req, option: o,
                                      decision: "answer" }),
  }, `${o.recommended ? "★ " : ""}${o.label} — ${o.effect}`));

  return R.createElement("section", {
    "data-testid": "office-boardroom-panel",
    "data-request": req.id,
    "data-class": req.requestClass,
    "data-urgency": req.urgency,
    role: "region", "aria-label": "Boardroom",
  }, [
    R.createElement("div", { key: "who", "data-testid": "boardroom-asking" },
      `${req.askingAgent} asks · ${req.urgency}`),
    R.createElement("p", { key: "sum", "data-testid": "boardroom-summary" },
      req.summary),
    R.createElement("div", { key: "opts", "data-testid": "boardroom-options" },
      optionRows),
    R.createElement("p", { key: "rec",
      "data-testid": "boardroom-recommendation" }, req.recommendation),
    R.createElement("div", { key: "aff", "data-testid": "boardroom-affected" },
      req.affected.map((a) => R.createElement("div", {
        key: `${a.kind}:${a.id}`,
        "data-testid": `boardroom-affected-${String(a.id)}`,
        "data-value": a.value,
      }, `${a.label}: ${a.value}`))),
    req.artifact
      ? R.createElement("div", { key: "art", "data-testid": "boardroom-artifact",
          "data-ref": req.artifact.ref },
          `${req.artifact.label} (${req.artifact.kind}: ${req.artifact.ref})`)
      : null,
    R.createElement("div", { key: "ev", "data-testid": "boardroom-evidence",
      style: { fontSize: "var(--office-fs-xs)",
               color: "var(--office-muted)" } }, req.evidence),
  ]);
}
