// Stable, readable per-bot identity colors for Bot Mode rooms. Pure.
//
// Spec correction (documented): five picked hues inside a 10 degree window
// cannot be placed pairwise >= 25 degrees apart within a +/-40 degree drift
// budget (that needs a 100 degree span; the budget covers 80). Distinct,
// readable colors win, so a picked member's drift is extended by the smallest
// amount that still fits, and only when a gap is otherwise unreachable.

const MIN_GAP = 25;
const MAX_PICK_DRIFT = 40;
const MAX_DRIFT_SEARCH = 180;
const REF_BG = { dark: [21, 23, 28], light: [255, 255, 255] };

export function parseColorToHsl(value) {
  const s = String(value ?? '').trim().toLowerCase();
  if (!s) return null;
  let m = s.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/);
  if (m) {
    const hex = m[1].length === 3 ? m[1].split('').map((c) => c + c).join('') : m[1];
    return rgbToHsl(parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16));
  }
  m = s.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/);
  if (m) return rgbToHsl(+m[1], +m[2], +m[3]);
  m = s.match(/^hsla?\(\s*(-?[\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%/);
  if (m) return { h: ((Math.round(+m[1]) % 360) + 360) % 360, s: Math.round(+m[2]), l: Math.round(+m[3]) };
  return null;
}

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b); const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0; let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h *= 60;
  }
  return { h: Math.round(h) % 360, s: Math.round(s * 100), l: Math.round(l * 100) };
}

function hslToRgb(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

function luminance([r, g, b]) {
  const c = [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

export function contrastRatio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

export function stableHue(name) {
  let h = 2166136261;
  for (const ch of String(name ?? '')) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); }
  return Math.abs(h) % 360;
}

const circ = (a, b) => { const d = Math.abs(a - b) % 360; return Math.min(d, 360 - d); };

function clampInk(h, s, mode) {
  const bg = REF_BG[mode] || REF_BG.dark;
  const sat = Math.max(55, Math.min(85, Number.isFinite(s) ? s : 70));
  const step = mode === 'light' ? -2 : 2;
  let l = mode === 'light' ? 38 : 72;
  for (let i = 0; i < 80; i += 1) {
    if (contrastRatio(hslToRgb(h, sat, l), bg) >= 4.5) return { sat, l };
    l += step;
    if (l <= 0 || l >= 100) return { sat, l: Math.max(0, Math.min(100, l)) };
  }
  return { sat, l: Math.max(0, Math.min(100, l)) };
}

// Smallest |offset| hue from base that keeps MIN_GAP from every placed hue.
function findHue(base, placed, limit) {
  for (let d = 0; d <= limit; d += 1) {
    const offsets = d === 0 ? [0] : [d, -d];
    for (const off of offsets) {
      const hue = ((base + off) % 360 + 360) % 360;
      if (placed.every((p) => circ(p, hue) >= MIN_GAP)) return hue;
    }
  }
  return null;
}

export function assignRoomIdentities(members = [], { mode = 'dark' } = {}) {
  const m = mode === 'light' ? 'light' : 'dark';
  const list = Array.isArray(members) ? members : [];
  const rows = list
    .map((member) => {
      const picked = parseColorToHsl(member?.color);
      const name = String(member?.profileName ?? '');
      return {
        profileName: name,
        base: picked ? picked.h : stableHue(name),
        s: picked?.s ?? 70,
        picked: Boolean(picked),
      };
    })
    .sort((a, b) => a.profileName.localeCompare(b.profileName));

  const placed = [];
  for (const row of rows) {
    let hue = findHue(row.base, placed, row.picked ? MAX_PICK_DRIFT : MAX_DRIFT_SEARCH);
    if (hue === null) hue = findHue(row.base, placed, MAX_DRIFT_SEARCH);
    if (hue === null) hue = row.base;
    placed.push(hue);
    row.hue = hue;
  }

  const byName = new Map(rows.map((r) => [r.profileName, r]));
  return list.map((member) => {
    const r = byName.get(String(member?.profileName ?? ''));
    const { sat, l } = clampInk(r.hue, r.s, m);
    return {
      profileName: r.profileName,
      hue: r.hue,
      ink: `hsl(${r.hue} ${sat}% ${l}%)`,
      bar: `hsl(${r.hue} ${Math.max(55, sat)}% ${m === 'light' ? 45 : 60}%)`,
      tint: `hsl(${r.hue} ${sat}% ${m === 'light' ? 50 : 55}%)`,
    };
  });
}
// Older room rows were stored with only a display label ("Roxas") and no
// profile speaker, so identity lookups by exact profile name missed them.
// Resolve a record to a room member by speaker, then by label, matching the
// profile name or display title case-insensitively. "default" and the
// canonical primary label map onto each other. Returns the member or null.
export function resolveRoomSpeaker(record = {}, members = []) {
  const list = Array.isArray(members) ? members : [];
  const norm = (value) => String(value ?? '').trim().toLowerCase();
  const aliases = (value) => {
    const v = norm(value);
    return v === 'roxas' || v === 'default' ? ['roxas', 'default'] : [v];
  };
  for (const raw of [record?.speaker, record?.roleLabel]) {
    const wanted = aliases(raw).filter(Boolean);
    if (!wanted.length) continue;
    const hit = list.find((member) => {
      const own = [...aliases(member?.name), ...aliases(member?.title)];
      return wanted.some((w) => own.includes(w));
    });
    if (hit) return hit;
  }
  return null;
}
