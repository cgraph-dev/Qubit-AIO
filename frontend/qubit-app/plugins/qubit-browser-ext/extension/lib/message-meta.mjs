// Pure helpers for message times, day dividers, and action rail placement.
// No DOM, no globals: safe to unit test and to reuse in the full-tab surface.

const MIN_VALID_MS = Date.UTC(2000, 0, 1);
const SECONDS_CUTOFF = 1e11; // below this a value is unix seconds
const DAY_MS = 86400000;
const RAIL_GAP = 8;
const RAIL_SAFETY = 8;
const RAIL_HYSTERESIS = 8;

// Intl output can use narrow no-break / no-break spaces before AM/PM; normalize
// them so tests and downstream string compares see regular spaces.
const tidy = (value) => String(value || '').replace(/[\u202f\u00a0]/g, ' ').trim();

// Accept ms or unix seconds; return a finite ms value inside a sane window, or
// null. Unknown must stay unknown: never fall back to "now" or 1970.
export function normalizeMessageTimestamp(value, { now = Date.now() } = {}) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
  const n = typeof value === 'number'
    ? value
    : (typeof value === 'string' && value.trim() ? Number(value) : NaN);
  if (!Number.isFinite(n) || n <= 0) return null;
  const ms = n < SECONDS_CUTOFF ? Math.round(n * 1000) : Math.round(n);
  if (!Number.isFinite(ms) || ms < MIN_VALID_MS) return null;
  if (Number.isFinite(now) && ms > now + DAY_MS) return null;
  return ms;
}

export function dayKey(ms) {
  if (!Number.isFinite(ms)) return '';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

function startOfDay(ms) {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function daysAgo(ms, now) {
  return Math.round((startOfDay(now) - startOfDay(ms)) / DAY_MS);
}

function fmt(locale, options, ms) {
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return '';
  try {
    return tidy(new Intl.DateTimeFormat(locale || undefined, options).format(date));
  } catch {
    try {
      return tidy(new Intl.DateTimeFormat(undefined, options).format(date));
    } catch {
      return '';
    }
  }
}

export function formatMessageTime(ms, { now = Date.now(), locale } = {}) {
  if (!Number.isFinite(ms)) return '';
  const time = fmt(locale, { hour: 'numeric', minute: '2-digit' }, ms);
  if (!time) return '';
  const ago = daysAgo(ms, now);
  if (ago <= 0) return time;
  if (ago <= 6) return `${fmt(locale, { weekday: 'short' }, ms)} ${time}`;
  const sameYear = new Date(ms).getFullYear() === new Date(now).getFullYear();
  const date = fmt(locale, sameYear
    ? { month: 'short', day: 'numeric' }
    : { month: 'short', day: 'numeric', year: 'numeric' }, ms);
  return `${date}, ${time}`;
}

export function formatMessageTimeFull(ms, { locale } = {}) {
  if (!Number.isFinite(ms)) return '';
  return fmt(locale, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit',
  }, ms);
}

export function dayDividerLabel(ms, previousMs, { now = Date.now(), locale, translate = (s) => s } = {}) {
  if (!Number.isFinite(ms)) return '';
  if (Number.isFinite(previousMs) && dayKey(previousMs) === dayKey(ms)) return '';
  const ago = daysAgo(ms, now);
  if (ago <= 0) return translate('Today');
  if (ago === 1) return translate('Yesterday');
  if (ago <= 6) return fmt(locale, { weekday: 'long' }, ms);
  const sameYear = new Date(ms).getFullYear() === new Date(now).getFullYear();
  return fmt(locale, sameYear
    ? { month: 'short', day: 'numeric' }
    : { month: 'short', day: 'numeric', year: 'numeric' }, ms);
}

export function decideRailPlacement({ rowWidth = 0, bubbleWidth = 0, railWidth = 0, previous = 'below' } = {}) {
  if (!(rowWidth > 0) || !(bubbleWidth > 0) || !(railWidth > 0)) return 'below';
  const free = rowWidth - bubbleWidth;
  const need = railWidth + RAIL_GAP + RAIL_SAFETY;
  if (previous === 'left') return free >= need - RAIL_HYSTERESIS ? 'left' : 'below';
  return free >= need + RAIL_HYSTERESIS ? 'left' : 'below';
}