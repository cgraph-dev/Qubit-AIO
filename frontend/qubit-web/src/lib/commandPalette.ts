/**
 * Command-palette catalog + ranking for `/qubit-chat`.
 *
 * `/qubit-chat` is a VOICE + REPORT surface, so its palette is not a coding
 * launcher (no diff staging, no hunk review — those belong to the terminal and
 * would duplicate `git`). It is a keyboard twin of the page's own affordances:
 * run a page action, jump to a session, insert a slash command, or open a
 * report — the same things an operator can say out loud, reachable by Ctrl-K.
 *
 * The catalog is pure data (no React, no DOM) so the ranking contract is
 * unit-tested directly, and the palette component only renders it.
 */
import { fuzzyScore } from "@/lib/fuzzy";

export type PaletteItemKind = "action" | "session" | "command" | "report";

export interface PaletteItem {
  /** Stable id, unique within a catalog build (used as the React key). */
  id: string;
  kind: PaletteItemKind;
  /** Primary display text. */
  label: string;
  /** Secondary text (a hint, a session id, a command description). */
  detail?: string;
  /** Extra fuzzy-searchable text beyond `label` + `detail`. */
  keywords?: string;
  /** Placeholder row shown while an async source is still loading. */
  disabled?: boolean;
}

const KIND_WEIGHT: Record<PaletteItemKind, number> = {
  action: 4,
  command: 3,
  report: 2,
  session: 1,
};

/**
 * Rank items by fuzzy query against label/detail/keywords, dropping
 * non-matches. An empty query returns every item in original order (kind
 * ordering preserved by the caller). Ties break by kind weight, then by the
 * original index so equal scores stay stable across renders.
 */
export function rankPaletteItems(
  items: readonly PaletteItem[],
  query: string,
): PaletteItem[] {
  const trimmed = query.trim();
  if (!trimmed) return [...items];

  const scored: Array<{ item: PaletteItem; score: number; index: number }> = [];
  items.forEach((item, index) => {
    if (item.disabled) return;
    const haystack = [item.label, item.detail ?? "", item.keywords ?? ""].join(
      " ",
    );
    const match = fuzzyScore(haystack, trimmed);
    if (!match) return;
    scored.push({ item, score: match.score, index });
  });

  scored.sort(
    (a, b) =>
      b.score - a.score ||
      KIND_WEIGHT[b.item.kind] - KIND_WEIGHT[a.item.kind] ||
      a.index - b.index,
  );
  return scored.map((s) => s.item);
}

/**
 * Move a selection index by `delta`, wrapping within `[0, length)`. Returns 0
 * for an empty list so callers never index past the end.
 */
export function movePaletteIndex(
  current: number,
  delta: number,
  length: number,
): number {
  if (length <= 0) return 0;
  const next = (current + delta) % length;
  return next < 0 ? next + length : next;
}
