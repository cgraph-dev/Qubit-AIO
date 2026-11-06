/**
 * Session-sidebar grouping — pure functions over real timestamps.
 *
 * The sidebar groups conversations the way the absorbed surface did
 * (`static/sessions.js:10989-11050` upstream: PINNED first, then calendar
 * buckets), but every label here is derived from the row's own
 * `last_active`, never from a server-supplied string and never from a bucket
 * the store does not have. A row with no timestamp lands in `older`, which is
 * the honest answer: its age is unknown, so it is not claimed to be today's.
 *
 * Kept free of React so the bucketing can be tested directly and so the render
 * tree stays a thin mapping over the groups this returns.
 */

import type { WorkbenchSessionRow } from "@/lib/workbench-api";

/** Section identity, stable across renders (used for collapse state keys). */
export type SessionGroupKey = "pinned" | "today" | "yesterday" | "week" | "lastWeek" | "older";

export interface SessionGroup {
  key: SessionGroupKey;
  /** Text the header shows. Upper-cased by CSS, not by the string. */
  label: string;
  /** Pinned groups read differently (accent colour) in the header. */
  pinned: boolean;
  rows: WorkbenchSessionRow[];
}

/** Local-midnight boundaries for "today", "this week" and "last week". */
export interface CalendarBounds {
  startOfToday: number;
  startOfWeek: number;
  startOfLastWeek: number;
}

/**
 * Week starts on Monday, matching the store's own calendar reading of a week
 * and avoiding the "last week" of a Sunday-based week beginning six days ago
 * while still being labelled last week.
 */
export function calendarBounds(nowMs: number): CalendarBounds {
  const now = new Date(nowMs);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  // getDay(): 0 = Sunday. Shift so Monday = 0, then walk back that many days.
  const dayShift = (now.getDay() + 6) % 7;
  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfWeek.getDate() - dayShift);
  const startOfLastWeek = new Date(startOfWeek);
  startOfLastWeek.setDate(startOfLastWeek.getDate() - 7);
  return {
    startOfToday,
    startOfWeek: startOfWeek.getTime(),
    startOfLastWeek: startOfLastWeek.getTime(),
  };
}

/** The bucket one timestamp falls in. Exported so tests can pin the edges. */
export function bucketFor(tsMs: number | null, nowMs: number): SessionGroupKey {
  if (tsMs === null || !Number.isFinite(tsMs)) return "older";
  const { startOfToday, startOfWeek, startOfLastWeek } = calendarBounds(nowMs);
  if (tsMs >= startOfToday) return "today";
  if (tsMs >= startOfToday - 86_400_000) return "yesterday";
  if (tsMs >= startOfWeek) return "week";
  if (tsMs >= startOfLastWeek) return "lastWeek";
  return "older";
}

const LABEL: Record<SessionGroupKey, string> = {
  pinned: "Pinned",
  today: "Today",
  yesterday: "Yesterday",
  week: "This week",
  lastWeek: "Last week",
  older: "Older",
};

/** Render order, newest bucket first, with pinned heading the list. */
const ORDER: readonly SessionGroupKey[] = ["today", "yesterday", "week", "lastWeek", "older"];

/** Newest first; rows without a timestamp sort last rather than first. */
export function byRecency(a: WorkbenchSessionRow, b: WorkbenchSessionRow): number {
  return (b.last_active ?? -1) - (a.last_active ?? -1);
}

/**
 * Sort, split pinned out, and bucket the rest.
 *
 * `pinned` is supplied by the caller because pinning on this page is a client
 * preference: the store has no pinned column of its own on this seam, so the
 * page owns the set and this function does not pretend to read one.
 */
export function groupSessions(
  rows: readonly WorkbenchSessionRow[],
  pinned: ReadonlySet<string>,
  nowMs: number,
): SessionGroup[] {
  const sorted = [...rows].sort(byRecency);
  const pinnedRows = sorted.filter((row) => pinned.has(row.id));
  const rest = sorted.filter((row) => !pinned.has(row.id));

  const groups: SessionGroup[] = [];
  if (pinnedRows.length > 0) {
    groups.push({ key: "pinned", label: LABEL.pinned, pinned: true, rows: pinnedRows });
  }
  const buckets = new Map<SessionGroupKey, WorkbenchSessionRow[]>();
  for (const row of rest) {
    const key = bucketFor(row.last_active === null ? null : row.last_active * 1000, nowMs);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(row);
    else buckets.set(key, [row]);
  }
  for (const key of ORDER) {
    const bucket = buckets.get(key);
    if (bucket && bucket.length > 0) {
      groups.push({ key, label: LABEL[key], pinned: false, rows: bucket });
    }
  }
  return groups;
}

/**
 * Compact relative age, the way the absorbed sidebar showed it (`9h`, `1d`,
 * `2d`, `1w`, `3mo`). Distinct from the house `timeAgo`, which spells out
 * "9h ago": in a 300px column the compact form is what fits beside a badge.
 */
export function compactAge(tsSeconds: number | null, nowMs: number): string {
  if (tsSeconds === null) return "";
  const delta = Math.max(0, nowMs / 1000 - tsSeconds);
  if (delta < 60) return "now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m`;
  if (delta < 86_400) return `${Math.floor(delta / 3600)}h`;
  if (delta < 86_400 * 7) return `${Math.floor(delta / 86_400)}d`;
  if (delta < 86_400 * 30) return `${Math.floor(delta / (86_400 * 7))}w`;
  if (delta < 86_400 * 365) return `${Math.floor(delta / (86_400 * 30))}mo`;
  return `${Math.floor(delta / (86_400 * 365))}y`;
}

/** Title shown on a row: the stored title, else a trimmed preview, else the id. */
export function sessionLabel(row: WorkbenchSessionRow): string {
  const title = row.title?.trim();
  // The store sometimes writes the session id in as the title (cron rows do).
  if (title && title !== "Untitled" && title !== row.id) return title;
  const preview = row.preview?.trim();
  if (preview) return preview.length > 80 ? `${preview.slice(0, 80)}…` : preview;
  return row.id;
}
