/**
 * Human-facing date/time labels.
 *
 * Framework-neutral pure helpers (Intl + Date only). Ported from the pinned
 * upstream mobile surface's `ui.tsx` (`dateLabel`, `timeLabel`,
 * `relativeDate`).
 */

export function dateLabel(value: string, options?: Intl.DateTimeFormatOptions): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-US", options || { month: "short", day: "numeric" });
}

export function timeLabel(value: string, timeZone?: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone });
}

export function relativeDate(value: string): string {
  const diff = Date.now() - new Date(value).getTime();
  return diff < 60_000
    ? "Just now"
    : diff < 3600_000
      ? `${Math.floor(diff / 60_000)}m ago`
      : diff < 86400_000
        ? `${Math.floor(diff / 3600_000)}h ago`
        : dateLabel(value);
}
