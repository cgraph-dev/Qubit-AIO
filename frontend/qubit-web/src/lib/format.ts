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

/**
 * Render a product version for display without doubling the product name or
 * the `v` prefix. The Qubit boundary reports values like `qubit-0.0.1`, the
 * system-stats payload reports a bare `1.0.0`, and some payloads already carry
 * a `v`; all three should read `v0.0.1` / `v1.0.0`, never `vqubit-0.0.1`.
 *
 * Non-version strings (e.g. `unknown`) pass through unchanged, and an absent
 * value reads as an em dash so callers never print `vundefined`.
 */
export function formatProductVersion(value: string | null | undefined): string {
  if (value == null) return "—";
  const trimmed = String(value).trim();
  if (!trimmed) return "—";
  const bare = trimmed
    .replace(/^qubit[-_\s]*/i, "")
    .replace(/^v(?=\d)/i, "");
  const looksLikeVersion = /^\d/.test(bare);
  return looksLikeVersion ? `v${bare}` : trimmed;
}

/**
 * Normalize an epoch timestamp to milliseconds.
 *
 * Qubit backends are not consistent: most dates are ISO strings or already in
 * milliseconds, but a few stores (notably the skill curator) publish epoch
 * SECONDS. `new Date(seconds)` silently renders 1970, so callers go through
 * this single normalizer instead. A 10-digit value is seconds, a 13-digit
 * value is milliseconds; ISO strings are parsed as-is. Returns undefined for
 * absent/unparseable input rather than inventing a date.
 */
export function toEpochMilliseconds(
  value: number | string | null | undefined,
): number | undefined {
  if (value == null || value === "") return undefined;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0) return undefined;
    return value < 100_000_000_000 ? value * 1000 : value;
  }
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) {
    const numeric = Number(trimmed);
    if (!Number.isFinite(numeric) || numeric <= 0) return undefined;
    return numeric < 100_000_000_000 ? numeric * 1000 : numeric;
  }
  const parsed = Date.parse(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** Locale-formatted date for an epoch value that may be seconds or ms. */
export function formatEpochTimestamp(
  value: number | string | null | undefined,
  locale?: string,
): string | null {
  const ms = toEpochMilliseconds(value);
  if (ms === undefined) return null;
  try {
    return new Date(ms).toLocaleString(locale);
  } catch {
    return new Date(ms).toLocaleString();
  }
}
