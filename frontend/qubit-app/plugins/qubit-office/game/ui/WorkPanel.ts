/* F12 — the work panel: tasks, proposals, evidence, and the money folds.
 *
 * SOURCES, ALL EXISTING (no invented data source):
 *   * money    — `hud` (the org fold), `history` / `history_totals` (the daily
 *                series and its sum). Every figure is the SAME fold the CLI
 *                reads (`pnl_source` says so, and is carried not paraphrased).
 *   * work     — the `characters[].last_event`, `stalled` and `dossier` the
 *                tower already carries. A proposal is a seat claiming work it
 *                has not had verified; the tower surfaces exactly that as
 *                `task_unverified` in the activity feed, and the panel groups
 *                the work state from there.
 *   * evidence — `characters[].dossier.beliefs[].evidence` and the activity
 *                rows' text. Evidence is shown with its provenance string,
 *                never as a bare claim.
 *
 * WHAT THIS PANEL DOES NOT DO (C4): it never POSTs to a store route, never
 * shows optimistic state, and never resolves its own confirmation. A stage it
 * initiates is a REQUEST via `commands.ts`; the receipt the authority reads back
 * is the only thing that becomes a settled fact.
 */

import type {
  ActivityRow, HistoryRow, OfficeCharacter, OfficeTowerPayload,
} from "../types";
import {
  foldFromCents, moneyRate, runwayView, type MoneyFold, type RunwayView,
} from "./money";
import {
  type CommandState, type StageAck, type SettleReceipt, type StagedOp,
  applySettleReceipt, applyStageAck, stageCommand, stageRequest,
} from "./commands";

// ---------------------------------------------------------------------------
// Money folds — read from the payload, never recomputed into a different number
// ---------------------------------------------------------------------------
export interface WorkMoney {
  revenue: MoneyFold;
  cost: MoneyFold;
  net: MoneyFold;
  burn: string;
  runway: RunwayView;
  /** The backend sentence naming the fold (carried, not paraphrased). */
  source: string;
  /** True when ANY fold is stale — the panel marks the whole strip. */
  anyStale: boolean;
}

/**
 * The money folds. `asOf` is the instant the fold was computed, `now` is the
 * payload's own clock, and `windowS` is `active_window_s` (the freshness
 * window).
 *
 * WHY `asOf` IS A SEPARATE INPUT, NOT JUST `payload.now`: a fold computed at the
 * same instant as the clock it is measured against can NEVER be stale, so a
 * panel that assumed `asOf === now` could not mark a stale figure even when the
 * source was old — the staleness rule would be decorative. The caller passes the
 * timestamp the fold actually carries; when it passes none, the fold is UNDATED
 * and therefore treated as stale (an unknown age is not "current").
 * `opts.trustNow` is the one case where a fold may be dated at `now`: a source
 * that reports a live fold with no timestamp of its own.
 *
 * HISTORY_TOTALS IS A CROSS-CHECK, NOT A SECOND TRUTH. `hud.net_cents` is the
 * org fold; `history_totals.net_cents` is the sum of the daily series. They
 * should agree, and when they do not the panel says so rather than picking one.
 */
export function workMoney(
  payload: OfficeTowerPayload | null,
  opts: { asOf?: number | null; trustNow?: boolean } = {},
): WorkMoney | null {
  if (!payload || !payload.hud) return null;
  const h = payload.hud;
  const now = payload.now;
  const win = payload.active_window_s;
  const source = String(h.pnl_source ?? "");
  const asOf = opts.asOf !== undefined
    ? opts.asOf
    : (opts.trustNow === false ? null : now);
  const common = { asOf, now, windowS: win, source };
  const revenue = foldFromCents("Revenue", h.revenue_cents, common);
  const cost = foldFromCents("Cost", h.cost_cents, common);
  const net = foldFromCents("Net", h.net_cents, common);
  return {
    revenue,
    cost,
    net,
    burn: moneyRate(h.burn_cents_per_day).text,
    runway: runwayView(h.runway_days, h.runway_is_derived === true),
    source,
    anyStale: revenue.staleness.stale || cost.staleness.stale
      || net.staleness.stale,
  };
}

/**
 * Do the two folds agree? The org fold and the history sum are computed from the
 * same ledger by different queries; a disagreement is real information.
 */
export function foldAgreement(
  payload: OfficeTowerPayload | null,
): { agree: boolean; hud: number | null; history: number | null } {
  if (!payload || !payload.hud || !payload.history_totals) {
    return { agree: true, hud: null, history: null };
  }
  const hud = Number(payload.hud.net_cents);
  const hist = Number(payload.history_totals.net_cents);
  return { agree: hud === hist, hud, history: hist };
}

/** The daily series the panel charts. Read verbatim; a zero day is a zero day. */
export function dailySeries(payload: OfficeTowerPayload | null): HistoryRow[] {
  if (!payload || !Array.isArray(payload.history)) return [];
  return payload.history;
}

// ---------------------------------------------------------------------------
// Work state — derived from the characters + activity the payload carries
// ---------------------------------------------------------------------------
export interface WorkItem {
  agentId: number;
  slug: string;
  seatType: string;
  state: "working" | "stalled" | "idle";
  /** A seat that claimed work with nothing to check it (task_unverified). */
  unverified: boolean;
  /** The seat's last event, verbatim. */
  lastEventKind: string;
  lastEventAgeS: number | null;
}

/** One work row per seat, from the character object. No synthesis. */
export function workItems(payload: OfficeTowerPayload | null): WorkItem[] {
  if (!payload || !Array.isArray(payload.characters)) return [];
  const unverifiedIds = new Set<number>(
    (payload.activity ?? [])
      .filter((a) => a.kind === "task_unverified")
      .map((a) => a.agent_id),
  );
  return payload.characters.map((c: OfficeCharacter) => ({
    agentId: c.agent_id,
    slug: c.slug,
    seatType: c.seat_type,
    state: c.stalled ? "stalled"
      : c.animation === "working" ? "working" : "idle",
    unverified: unverifiedIds.has(c.agent_id),
    lastEventKind: c.last_event ? String(c.last_event.kind) : "",
    lastEventAgeS: c.last_event ? Number(c.last_event.age_s) : null,
  }));
}

/**
 * The proposals outstanding: seats whose work awaits a check. A proposal is a
 * RECORD the dispatcher cannot see, but what the tower exposes is its STATE —
 * `task_unverified` — so that is what is grouped, honestly, as "awaiting a
 * check", not as "a proposal to approve" (which would be a kind the tower
 * does not carry).
 */
export function outstandingProposals(
  payload: OfficeTowerPayload | null,
): WorkItem[] {
  return workItems(payload).filter((w) => w.unverified);
}

/** Evidence rows: a claim and the provenance string the payload gives it. */
export interface EvidenceRow {
  agentId: number;
  slug: string;
  dimension: string;
  belief: string;
  evidence: string;
}

export function evidenceRows(
  payload: OfficeTowerPayload | null,
): EvidenceRow[] {
  if (!payload || !Array.isArray(payload.characters)) return [];
  const out: EvidenceRow[] = [];
  for (const c of payload.characters) {
    const beliefs = c.dossier?.beliefs ?? [];
    for (const b of beliefs) {
      out.push({
        agentId: c.agent_id, slug: c.slug,
        dimension: String(b.dimension), belief: String(b.belief),
        evidence: String(b.evidence),
      });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Stage / settle — the C4-safe flow, delegating to `commands.ts`
// ---------------------------------------------------------------------------
export interface WorkStageInput {
  action: string;
  targetKind: string;
  targetId: string | number;
  args?: Record<string, string | number | boolean | null>;
  /** The revision the stage is aimed at. `null` when the caller did not pin one. */
  revision: number | null;
}

/**
 * Ask to stage. Returns the next command state and the request the caller hands
 * to the authority. A duplicate (a second click) produces NO second stage.
 */
export function stageWork(
  state: CommandState,
  input: WorkStageInput,
): { state: CommandState; request: ReturnType<typeof stageRequest>;
     duplicate: boolean; reason: string } {
  const op: StagedOp = {
    action: input.action,
    targetKind: input.targetKind,
    targetId: input.targetId,
    args: input.args ?? {},
  };
  const res = stageCommand(state, op, input.revision);
  if (res.kind === "duplicate") {
    return {
      state: res.state, request: stageRequest(op, input.revision),
      duplicate: true,
      reason: `already staged as ${res.command.idempotencyKey}`,
    };
  }
  if (res.kind === "estop") {
    return {
      state: res.state, request: stageRequest(op, input.revision),
      duplicate: false, reason: res.reason,
    };
  }
  return {
    state: res.state, request: stageRequest(op, input.revision),
    duplicate: false, reason: "",
  };
}

export function ackWork(state: CommandState, ack: StageAck): CommandState {
  return applyStageAck(state, ack);
}

export function settleWork(
  state: CommandState,
  receipt: SettleReceipt,
): CommandState {
  return applySettleReceipt(state, receipt);
}

// ---------------------------------------------------------------------------
// Render — uses the HOST's React (no `react` import; see the shell header).
// ---------------------------------------------------------------------------
type ReactLike = { createElement: (...args: unknown[]) => unknown };

export interface WorkPanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
  /** The staged-command state, owned by the shell. */
  commands?: CommandState;
}

export function renderWorkPanel(props: WorkPanelProps): unknown {
  const { R, payload } = props;
  const money = workMoney(payload);
  const agree = foldAgreement(payload);
  const items = workItems(payload);
  const proposals = outstandingProposals(payload);

  const moneyStrip = money
    ? R.createElement("div", {
        key: "money", "data-testid": "work-money",
        "data-stale": String(money.anyStale),
        role: "group", "aria-label": "Organisation money",
      }, [
        cell(R, "revenue", money.revenue),
        cell(R, "cost", money.cost),
        cell(R, "net", money.net),
        R.createElement("div", {
          key: "burn", "data-testid": "work-burn",
          "data-value": money.burn,
        }, `Burn ${money.burn}`),
        R.createElement("div", {
          key: "runway", "data-testid": "work-runway",
          "data-state": money.runway.state,
          "data-value": money.runway.text,
        }, `Runway ${money.runway.text}`),
        R.createElement("div", {
          key: "src", "data-testid": "work-money-source",
          style: { fontSize: "var(--office-fs-xs)",
                   color: "var(--office-muted)" },
        }, money.source),
      ])
    : R.createElement("div", { key: "money", "data-testid": "work-money-empty" },
        "No money fold in this payload.");

  // A disagreement between the org fold and the history sum is surfaced, not
  // resolved by picking a favourite.
  const disagree = !agree.agree
    ? R.createElement("div", {
        key: "disagree", role: "status", "data-testid": "work-money-disagree",
        style: { color: "var(--office-warn, #d9a02b)",
                 fontSize: "var(--office-fs-xs)" },
      }, `The org fold (${agree.hud}c) and the history sum (${agree.history}c) ` +
         `disagree.`)
    : null;

  const proposalList = proposals.length > 0
    ? R.createElement("ul", { key: "props", "data-testid": "work-proposals" },
        proposals.map((p) => R.createElement("li", {
          key: p.agentId, "data-testid": `work-proposal-${p.agentId}`,
        }, `${p.slug} claimed work with nothing to check it`)))
    : R.createElement("div", { key: "props", "data-testid": "work-proposals-empty" },
        "No work is awaiting a check.");

  const stalled = items.filter((w) => w.state === "stalled");

  return R.createElement("section", {
    "data-testid": "office-work-panel",
    role: "region", "aria-label": "Work and money",
  }, [
    moneyStrip,
    disagree,
    R.createElement("div", {
      key: "counts", "data-testid": "work-counts",
      "data-heads": String(items.length),
      "data-stalled": String(stalled.length),
      "data-awaiting": String(proposals.length),
    }, `${items.length} seats · ${stalled.length} stalled · ` +
       `${proposals.length} awaiting a check`),
    proposalList,
  ]);
}

function cell(R: ReactLike, key: string, fold: MoneyFold): unknown {
  return R.createElement("div", {
    key, "data-testid": `work-money-${key}`,
    "data-known": String(fold.amount.known),
    // A caller (and the tests) must be able to see staleness as data, not only
    // as styling.
    "data-stale": String(fold.staleness.stale),
    "data-value": fold.amount.text,
  }, [
    R.createElement("span", { key: "l", className: "office-game__stat-label" },
      fold.label),
    R.createElement("span", { key: "v" }, fold.amount.text),
    fold.staleness.stale
      ? R.createElement("span", {
          key: "s", role: "status",
          "data-testid": `work-money-${key}-stale`,
          style: { fontSize: "var(--office-fs-xs)",
                   color: "var(--office-warn, #d9a02b)" },
        }, "STALE")
      : null,
  ]);
}

/** Exported so a caller can assert the rules without a DOM. */
export function activityIsRoutine(kind: string, activity: ActivityRow[]): boolean {
  return activity.some((a) => a.kind === kind);
}
