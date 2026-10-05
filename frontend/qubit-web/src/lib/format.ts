/**
 * Format a token count as a human-readable string (e.g. 1M, 128K, 4096).
 * Strips trailing ".0" for clean round numbers.
 */
export function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1)}K`;
  return String(n);
}

/**
 * Split a millisecond duration into the whole units that elapsed, largest
 * first. Returns every unit from days down to seconds so callers can take the
 * leading one or two; callers decide how much precision to show.
 */
function _durationParts(ms: number): Array<{ value: number; unit: "day" | "hour" | "minute" | "second" }> {
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const parts: Array<{ value: number; unit: "day" | "hour" | "minute" | "second" }> = [];
  if (days) parts.push({ value: days, unit: "day" });
  if (hours) parts.push({ value: hours, unit: "hour" });
  if (minutes) parts.push({ value: minutes, unit: "minute" });
  if (seconds || parts.length === 0) parts.push({ value: seconds, unit: "second" });
  return parts;
}

/**
 * Compact elapsed duration for dense rows — "1h 5m", "3d 2h", "45s".
 *
 * Returns `undefined` for a non-finite or negative input so callers can render
 * their own dash rather than printing a bogus "0s" over missing data: an
 * unknown duration and a duration of zero are different facts.
 */
export function formatDurationCompact(ms: number | null | undefined): string | undefined {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return undefined;
  const parts = _durationParts(ms);
  const unit: Record<string, string> = { day: "d", hour: "h", minute: "m", second: "s" };
  return parts.slice(0, 2).map((p) => `${p.value}${unit[p.unit]}`).join(" ");
}

/**
 * Long-form elapsed duration for completed-work summaries — "1 hour, 5 minutes".
 *
 * Phrasing comes from `Intl` so a non-English locale gets its own unit words
 * and list separator rather than English words glued together. Signature is
 * `(ms, locale, fallback)` — the locale is the second argument because it is
 * the common override; the fallback is what to show for an unknown duration.
 */
export function formatDurationLong(
  ms: number | null | undefined,
  locale = "en",
  fallback = "—",
): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return fallback;
  const parts = _durationParts(ms);
  const list = new Intl.ListFormat(locale, { type: "unit", style: "long" });
  return list.format(
    parts.slice(0, 2).map((p) =>
      new Intl.NumberFormat(locale, {
        style: "unit",
        unit: p.unit,
        unitDisplay: "long",
      }).format(p.value),
    ),
  );
}
