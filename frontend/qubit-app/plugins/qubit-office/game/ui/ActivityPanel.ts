/* F12 — the activity panel: grouping and the de-noise rule.
 *
 * TWO JOBS, AND THE SECOND IS THE ONE THAT CAN HURT
 * -------------------------------------------------
 * 1. Group the `activity` rows by their REAL `kind`. The payload's activity
 *    feed (`tower.py::activity_feed`) carries `{id, ts, kind, agent_id,
 *    agent_slug, text}`; the kinds are whatever the backend emits
 *    (`run_started`, `run_settled`, `run_unpriced`, `task_unverified`,
 *    `tool_call`, ...). Grouping invents no kind the payload does not have.
 *
 * 2. **De-noise the routine without silencing the blocking.** C25 reserves the
 *    loud surface for mandatory reviews and blocking requests only, because at
 *    ~71 seats an undisciplined feed becomes noise the owner stops reading —
 *    "worse than silence, because it looks like it is working."
 *
 *    The de-noise rule must be a CLASSIFIER, not a mute. A `tool_call` is
 *    routine and collapses into a counted group. A request that needs a HUMAN —
 *    a blocking ask, an approval, an access/capability need — must survive ANY
 *    amount of de-noising, at any count, always. If the de-noiser can drop one,
 *    it is a machine for losing exactly the messages that matter.
 */

import type { ActivityRow, OfficeTowerPayload } from "../types";

/** How loud a row is allowed to be. Mirrors the `visible` vocabulary of C25. */
export type Visibility = "notify" | "badge" | "bubble" | "silent";

/**
 * The kinds that are ROUTINE. These are the ones de-noising may collapse. This
 * list is deliberately explicit and closed: an unknown kind is NOT assumed
 * routine, because assuming a stranger is quiet is how a blocking ask gets
 * dropped by a de-noiser that has never seen its class before.
 */
const ROUTINE_KINDS = new Set([
  "tool_call",
  "run_started",
  "run_settled",
  "run_unpriced",
]);

/**
 * The kinds that need a HUMAN. These may NEVER be de-noised away: they are
 * `notify` by C25, and they survive at any count.
 *
 * `task_unverified` is here rather than routine on purpose — it is the org
 * saying "a task was claimed with nothing to check it", which is a review the
 * owner asked for, not chatter.
 */
const HUMAN_KINDS = new Set([
  "blocking_request",
  "blocking",
  "review",
  "review_requested",
  "capability_request",
  "access_request",
  "approval_request",
  "escalation",
  "task_unverified",
]);

export interface ActivityGroup {
  kind: string;
  count: number;
  /** Newest row in the group. */
  latest: ActivityRow;
  /** The rows, newest first. Empty when the group is collapsed. */
  rows: ActivityRow[];
  /** The group that must be shown individually no matter how large. */
  needsHuman: boolean;
  /** The loud surface this group is allowed. */
  visibility: Visibility;
  /** True when routine rows were folded into a count rather than listed. */
  collapsed: boolean;
}

/** Is this kind one that needs a human? Never de-noised, whatever the count. */
export function needsHuman(kind: string): boolean {
  return HUMAN_KINDS.has(String(kind || "").toLowerCase());
}

/** Is this kind routine? Unknown kinds are NOT routine (fail loud, not quiet). */
export function isRoutine(kind: string): boolean {
  const k = String(kind || "").toLowerCase();
  return ROUTINE_KINDS.has(k) && !needsHuman(k);
}

/** The visibility a row is allowed, per C25. */
export function visibilityFor(kind: string): Visibility {
  if (needsHuman(kind)) return "notify";
  if (isRoutine(kind)) return "silent";
  // An unknown kind is surfaced quietly rather than hidden: it is neither a
  // human request (which must be loud) nor a known routine (which may be
  // silent), so it gets the middle surface.
  return "bubble";
}

/**
 * Group the activity feed. Routine kinds collapse to a counted group when they
 * repeat; human kinds are ALWAYS listed individually.
 *
 * `collapseRoutine` is the de-noise switch. It may fold routine noise; it may
 * NOT fold human requests — that is asserted in the tests, because a switch
 * that can mute a blocking ask is not a preference, it is a failure.
 */
export function groupActivity(
  rows: ActivityRow[],
  opts: { collapseRoutine?: boolean } = {},
): ActivityGroup[] {
  const collapse = opts.collapseRoutine !== false;
  const byKind = new Map<string, ActivityRow[]>();
  for (const r of rows) {
    const kind = String(r.kind ?? "");
    const list = byKind.get(kind) ?? [];
    list.push(r);
    byKind.set(kind, list);
  }
  const groups: ActivityGroup[] = [];
  for (const [kind, list] of byKind) {
    // Newest first within a group: the feed carries `ts`, so order is real.
    const sorted = [...list].sort((a, b) => b.ts - a.ts);
    const human = needsHuman(kind);
    const routine = isRoutine(kind);
    groups.push({
      kind,
      count: sorted.length,
      latest: sorted[0],
      // A human group always carries its rows. A routine group collapses them
      // when the switch is on AND there is more than one to fold.
      rows: human || !collapse ? sorted : [],
      needsHuman: human,
      visibility: visibilityFor(kind),
      collapsed: !human && routine && collapse && sorted.length > 1,
    });
  }
  // Human groups float to the top and sort by recency — the point of de-noising
  // is that the loud ones are the ones you actually see.
  groups.sort((a, b) => {
    if (a.needsHuman !== b.needsHuman) return a.needsHuman ? -1 : 1;
    return b.latest.ts - a.latest.ts;
  });
  return groups;
}

/**
 * The rows a de-noise must NEVER drop, however aggressive the collapsing. This
 * is the set the panel guarantees to the owner.
 */
export function preservedRows(
  rows: ActivityRow[],
  opts: { collapseRoutine?: boolean } = {},
): ActivityRow[] {
  const groups = groupActivity(rows, opts);
  const out: ActivityRow[] = [];
  for (const g of groups) {
    if (g.needsHuman) out.push(...g.rows);
  }
  return out;
}

/** Activity summary for a header line. Reads the payload verbatim. */
export interface ActivitySummary {
  total: number;
  needsHuman: number;
  routine: number;
  /** The kinds actually present, most numerous first. */
  kinds: Array<{ kind: string; count: number }>;
}

export function summariseActivity(rows: ActivityRow[]): ActivitySummary {
  const groups = groupActivity(rows, { collapseRoutine: false });
  const kinds = groups.map((g) => ({ kind: g.kind, count: g.count }))
    .sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind));
  return {
    total: rows.length,
    needsHuman: groups.filter((g) => g.needsHuman)
      .reduce((n, g) => n + g.count, 0),
    routine: groups.filter((g) => isRoutine(g.kind))
      .reduce((n, g) => n + g.count, 0),
    kinds,
  };
}

/** The payload's activity, or an empty feed. Never a fabricated row. */
export function activityRows(payload: OfficeTowerPayload | null): ActivityRow[] {
  if (!payload || !Array.isArray(payload.activity)) return [];
  return payload.activity;
}

/** A time string from the row's own epoch seconds. Presentation only. */
export function activityTime(ts: number): string {
  if (!Number.isFinite(ts)) return "unknown";
  return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19);
}

// ---------------------------------------------------------------------------
// Render — uses the HOST's React. No `import from "react"` anywhere (the shell
// header explains why: a second React copy silently splits the hook runtime).
// ---------------------------------------------------------------------------
type ReactLike = { createElement: (...args: unknown[]) => unknown };

export interface ActivityPanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
  /** De-noise routine noise. Human requests survive it — always. */
  collapseRoutine?: boolean;
}

/**
 * Render the activity panel. Routine groups collapse to a line with a count;
 * a human group is LISTED, with its rows, whatever the count.
 */
export function renderActivityPanel(props: ActivityPanelProps): unknown {
  const { R, payload } = props;
  const rows = activityRows(payload);
  const summary = summariseActivity(rows);
  const groups = groupActivity(rows, { collapseRoutine: props.collapseRoutine });

  const header = R.createElement("div", {
    key: "head", "data-testid": "activity-summary",
    "data-needs-human": String(summary.needsHuman),
    style: { fontSize: "var(--office-fs-sm)", marginBottom: "var(--office-s2)" },
  }, rows.length === 0
    ? "No activity yet."
    : `${summary.total} events · ${summary.needsHuman} need you`);

  const body = groups.map((g) => R.createElement("div", {
    key: g.kind,
    "data-testid": `activity-group-${g.kind}`,
    "data-visibility": g.visibility,
    "data-needs-human": String(g.needsHuman),
    "data-count": String(g.count),
    "data-collapsed": String(g.collapsed),
    className: "office-game__activity-row",
    style: {
      borderLeft: g.needsHuman
        ? "3px solid var(--office-warn, #d9a02b)"
        : "3px solid transparent",
      paddingLeft: "var(--office-s1)",
      marginBottom: "var(--office-s1)",
    },
  }, [
    R.createElement("div", { key: "l", style: { fontSize: "var(--office-fs-xs)",
      color: "var(--office-muted)" } },
      g.needsHuman ? `needs you · ${g.kind}`
        : g.collapsed ? `${g.kind} ×${g.count}`
        : g.kind),
    g.rows.length > 0
      ? R.createElement("ul", { key: "rows",
          "data-testid": `activity-rows-${g.kind}`,
          style: { margin: 0, paddingLeft: "var(--office-s3)" } },
        g.rows.map((r) => R.createElement("li", {
          key: String(r.id),
          "data-testid": `activity-row-${r.id}`,
        }, `${activityTime(r.ts)} · ${r.agent_slug}: ${r.text}`)))
      // A collapsed routine group still says WHAT it folded, never a bare count
      // with nothing behind it.
      : R.createElement("div", { key: "one", "data-testid": `activity-latest-${g.kind}`,
          style: { fontSize: "var(--office-fs-sm)" } },
        `latest: ${g.latest.text}`),
  ]));

  return R.createElement("section", {
    "data-testid": "office-activity-panel",
    "aria-label": "Activity",
    role: "region",
  }, [header, ...body]);
}
