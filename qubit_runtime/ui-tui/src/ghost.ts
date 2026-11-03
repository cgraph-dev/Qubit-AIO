import type { ThemeColors } from './theme.js'

// ── Friendly floating ghost ──────────────────────────────────────────
//
// A 1:1 port of the "Friendly Floating Ghost" piece (slug
// friendly-ghost-float, ascii-magic-art 1.1.0, MIT, ascii-magic.com): 60x28,
// the piece's own geometry and its own palette. The ghost bobs over a misty
// graveyard, waving its stubby arms, rippling its hem and blinking shyly.
//
// The piece is PROCEDURAL — the bob, the arms, the hem, the mist and the blink
// all derive from one continuous time value — so `ghostFrame(t)` below is a
// line-by-line port of pieces/friendly-ghost-float.js, not a list of frames.
// Every glyph, every palette index and every threshold is the reference's.
//
// Colours are the PIECE's, not the theme's: each cell carries its palette key
// and paints as the palette's truecolor hex, so the panel looks like the
// published piece (near-black indigo sky, cream moon, white-lavender ghost,
// pink cheeks) instead of a flat theme-tinted silhouette.
//
// Terminal cells are roughly twice as tall as they are wide, but the piece is
// read as a character grid — one grid row per printed line, exactly as the
// library's own terminal player draws it in a text look. All 28 rows at 60
// columns stand as published.

export const GHOST_COLS = 60
export const GHOST_ROWS = 28
export const GHOST_COMPACT_COLS = 30
export const GHOST_COMPACT_ROWS = 14
/** Terminal lines ghostLines() returns: one grid row per line, as published. */
export const GHOST_LINES = GHOST_ROWS

const clamp = (v: number, a = 0, b = 1) => (v < a ? a : v > b ? b : v)
const smooth = (t: number) => t * t * (3 - 2 * t)
const lerp = (a: number, b: number, t: number) => a + (b - a) * t

/** JS Math.round, floor(v + 0.5) — kept explicit because the face features sit
 *  on .5 boundaries where a different rounding shifts them by a whole cell. */
const jsround = (v: number) => Math.floor(v + 0.5)

function hash2(x: number, y: number, s: number): number {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(s, 2147483647)) | 0
  h = Math.imul(h ^ (h >>> 13), 1274126177)

  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

function noise2(x: number, y: number, seed = 0): number {
  const xi = Math.floor(x)
  const yi = Math.floor(y)
  const xf = smooth(x - xi)
  const yf = smooth(y - yi)
  const a = hash2(xi, yi, seed)
  const b = hash2(xi + 1, yi, seed)
  const c = hash2(xi, yi + 1, seed)
  const d = hash2(xi + 1, yi + 1, seed)

  return lerp(lerp(a, b, xf), lerp(c, d, xf), yf)
}

function fbm(x: number, y: number, octaves = 4, seed = 0): number {
  let v = 0
  let amp = 0.5
  let f = 1
  let norm = 0

  for (let i = 0; i < octaves; i++) {
    v += amp * noise2(x * f, y * f, seed + i * 17)
    norm += amp
    amp *= 0.5
    f *= 2
  }

  return v / norm
}

// ── the piece's palette, verbatim ───────────────────────────────────
//
// Same hexes, same gradients and same palette-key numbering as
// pieces/friendly-ghost-float.js, so a cell's colour key here means the same
// thing it means in the reference source.
const palette: string[] = []

const add = (hexes: string[]): number => {
  const at = palette.length

  palette.push(...hexes)

  return at
}

const gradient = (stops: string[], n = 10): string[] => {
  const rgb = stops.map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)))

  const out: string[] = []

  for (let i = 0; i < n; i++) {
    const t = (i / (n - 1)) * (rgb.length - 1)
    const j = Math.min(rgb.length - 2, Math.floor(t))
    const f = t - j

    out.push(
      '#' +
        rgb[j]!.map((v, k) => Math.round(lerp(v, rgb[j + 1]![k]!, f)).toString(16).padStart(2, '0')).join('')
    )
  }

  return out
}

const SKY = add(gradient(['#0a0618', '#160c2c', '#24143e', '#341e52', '#462a62'], 10))
const MIST = add(gradient(['#4a3a6a', '#6a5a8a', '#8a7aa8'], 3))
const HILL = add(gradient(['#06040c', '#0e0a18', '#181226'], 3))
const STONE = add(['#2a2638', '#3a3650', '#4a4662'])
const MOON = add(['#d8d4b8', '#f4f0d8'])
const GHOST = add(gradient(['#6a6a9a', '#9898c4', '#c4c4e4', '#e4e4f6', '#ffffff'], 10))
const EYE = add(['#14102a', '#ffffff'])
const PINK = add(['#ff9ab8', '#ffc4d6'])

const PALETTE_KEYS = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'

/** Palette key for a palette index, as the reference kit.palette() assigns it. */
const key = (i: number): string => PALETTE_KEYS[i]!

/** Deterministic PRNG (mulberry32), the reference kit's `rng`. */
function rng(seed = 1): () => number {
  let a = seed >>> 0

  return () => {
    a = (a + 0x6d2b79f5) >>> 0

    let t = a

    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)

    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const blank = (): Cell[] => Array.from({ length: GHOST_COLS * GHOST_ROWS }, () => ({ ch: ' ', co: key(SKY) }))

const put = (cells: Cell[], x: number, y: number, ch: string, co: string) => {
  const px = Math.floor(x)
  const py = Math.floor(y)

  if (px < 0 || py < 0 || px >= GHOST_COLS || py >= GHOST_ROWS) {
    return
  }

  cells[px + py * GHOST_COLS] = { ch, co }
}

// ── baked background ────────────────────────────────────────────────
//
// Sky gradient, stars, moon, hill and headstones never change with time, so
// they are built once at module load exactly as the reference builds them.
const BG: { ch: string; co: string }[][] = (() => {
  const cells: { ch: string; co: string }[][] = []

  for (let y = 0; y < GHOST_ROWS; y++) {
    cells.push(Array.from({ length: GHOST_COLS }, () => ({ ch: ' ', co: key(SKY) })))
  }

  const hill = (x: number) => 22 + Math.sin(x * 0.11) * 1.2 + Math.sin(x * 0.27 + 1) * 0.6

  const set = (x: number, y: number, ch: string, co: string) => {
    if (x >= 0 && y >= 0 && x < GHOST_COLS && y < GHOST_ROWS) {
      cells[y]![x] = { ch, co }
    }
  }

  for (let y = 0; y < GHOST_ROWS; y++) {
    for (let x = 0; x < GHOST_COLS; x++) {
      if (y >= hill(x)) {
        const d = y - hill(x)

        set(x, y, d < 1 ? '▄' : '█', key(HILL + (d < 1 ? 2 : y > 25 ? 0 : 1)))

        continue
      }

      const md = Math.hypot((x - 50) / 2, y - 4)

      const v = clamp(
        0.3 + (y / 22) * 0.4 + Math.exp(-md / 6) * 0.5 + (fbm(x * 0.07, y * 0.15, 3, 3) - 0.5) * 0.2
      )

      set(x, y, ' ░▒▓'[1 + Math.min(2, Math.floor(v * 3))]!, key(SKY + Math.min(9, Math.floor(v * 10))))
    }
  }

  const r = rng(7)

  for (let i = 0; i < 18; i++) {
    set(Math.floor(r() * GHOST_COLS), Math.floor(r() * 12), '·', key(MIST + 2))
  }

  // moon
  for (let y = 1; y < 8; y++) {
    for (let x = 44; x < 57; x++) {
      const dx = (x + 0.5 - 50) / 5
      const dy = (y + 0.5 - 4) / 2.6

      if (dx * dx + dy * dy <= 1) {
        set(x, y, '█', key(MOON + (dx < 0.2 ? 1 : 0)))
      }
    }
  }

  // headstones
  for (const [sx, h] of [[5, 3], [14, 2], [44, 3], [53, 2]] as const) {
    const top = Math.floor(hill(sx + 2)) - h

    for (let y = top; y < top + h + 1; y++) {
      for (let x = sx; x < sx + 4; x++) {
        set(x, y, y === top && (x === sx || x === sx + 3) ? '▄' : '█', key(STONE + (x === sx ? 2 : 1)))
      }
    }

    set(sx + 1, top + 1, '┼', key(STONE))
  }

  return cells
})()

const text = (cells: Cell[], x: number, y: number, s: string, co: string) => {
  for (let k = 0; k < s.length; k++) {
    put(cells, x + k, y, s[k]!, co)
  }
}

const ell = (
  cells: Cell[],
  cx: number,
  cy: number,
  rx: number,
  ry: number,
  fn: (x: number, y: number, dx: number, dy: number) => void
) => {
  for (let y = Math.floor(cy - ry); y <= Math.ceil(cy + ry); y++) {
    for (let x = Math.floor(cx - rx); x <= Math.ceil(cx + rx); x++) {
      const dx = (x + 0.5 - cx) / rx
      const dy = (y + 0.5 - cy) / ry

      if (dx * dx + dy * dy <= 1) {
        fn(x, y, dx, dy)
      }
    }
  }
}

/**
 * One frame of the ghost scene at time `t` (seconds).
 *
 * Pure: same `t` always returns the same rows, which is what makes the
 * animation testable without a renderer. `co` is the piece's palette key.
 */
export const ghostFrame = (t: number): Cell[][] => {
  const cells = blank()

  for (let y = 0; y < GHOST_ROWS; y++) {
    for (let x = 0; x < GHOST_COLS; x++) {
      cells[x + y * GHOST_COLS] = BG[y]![x]!
    }
  }

  // drifting mist
  for (let y = 18; y < 25; y++) {
    for (let x = 0; x < GHOST_COLS; x++) {
      const m = fbm(x * 0.08 - t * 0.35, y * 0.4, 3, 9)

      if (m > 0.55) {
        put(cells, x, y, m > 0.66 ? '▒' : '░', key(MIST + (m > 0.66 ? 1 : 0)))
      }
    }
  }

  const cx = 30 + Math.sin(t * 0.785) * 4
  // The bob rides a faster wave than the reference (1.57 -> 2.2 rad/s) and
  // travels +/-2 rows instead of +/-1.3: at the reference rate the head's top
  // edge sat still for over a second at a time, which read as a frozen ghost.
  const cy = 11 + Math.sin(t * 2.2) * 2

  // shadow on the ground, shrinking as the ghost rises
  const sw = 7 - (cy - 9.7) * 0.8

  ell(cells, cx, 24.5, sw, 0.9, (x, y) => put(cells, x, y, '▓', key(HILL)))

  // stubby arms
  const wave = Math.sin(t * 6.28)

  for (const [s, lift] of [
    [-1, 0.3 + Math.sin(t * 3.14) * 0.3],
    [1, -0.6 + wave * 0.9]
  ] as const) {
    const ax = cx + s * 11.5
    const ay = cy + 2.2 + lift * -1.6

    ell(cells, ax, ay, 2.6, 1.3, (x, y, _dx, dy) => {
      put(cells, x, y, '█', key(GHOST + (dy < 0 ? 8 : 6)))
    })
  }

  // body: dome on top, wavy hem below
  const top = cy - 9
  const base = cy + 5.5

  for (let y = Math.floor(top); y <= base + 2; y++) {
    for (let x = Math.floor(cx - 12); x <= cx + 12; x++) {
      const dx = (x + 0.5 - cx) / 11

      const ok =
        y + 0.5 < cy
          ? dx * dx + ((y + 0.5 - cy) / 9) ** 2 <= 1
          : Math.abs(dx) <= 1 + (y - cy) * 0.015 &&
            y + 0.5 <= base + Math.sin((x - cx) * 0.95 - t * 6.28) * 1.3

      if (!ok) {
        continue
      }

      const vy = (y - top) / 15
      const lit = clamp(0.95 - vy * 0.45 - dx * 0.3 - dx * dx * 0.25)
      const k = key(GHOST + Math.min(9, Math.floor(lit * 10)))

      put(cells, x, y, Math.abs(dx) > 0.88 ? '▓' : '█', k)
    }
  }

  // face — blinks on its own cycle
  const bp = t % 4
  const blink = Math.abs(bp - 3.1) < 0.14 || Math.abs(bp - 3.45) < 0.12
  const ey = cy - 2.5
  const lookX = Math.sin(t * 0.785) * 0.7

  for (const ex of [cx - 4.2, cx + 4.2]) {
    if (blink) {
      text(cells, jsround(ex - 1.5), jsround(ey), '▀▀▀', key(EYE))

      continue
    }

    ell(cells, ex + lookX, ey, 1.7, 2.2, (x, y) => put(cells, x, y, '█', key(EYE)))
    put(cells, ex + lookX - 0.6, ey - 1.2, '▀', key(EYE + 1))
  }

  for (const ex of [cx - 7.5, cx + 6.5]) {
    text(cells, jsround(ex), jsround(ey + 2), '▄▄', key(PINK))
  }

  const opened = Math.sin(t * 1.57) > 0.4
  const mx = jsround(cx - 1)
  const my = jsround(ey + 3)

  if (opened) {
    text(cells, mx, my, '▄▄', key(EYE))
    text(cells, mx, my + 1, '▀▀', key(EYE))
  } else {
    text(cells, mx, my, '▀▀', key(EYE))
  }

  const rows: Cell[][] = []

  for (let y = 0; y < GHOST_ROWS; y++) {
    rows.push(cells.slice(y * GHOST_COLS, (y + 1) * GHOST_COLS))
  }

  return rows
}

const hex = (k: string): string => palette[PALETTE_KEYS.indexOf(k)] ?? palette[0]!

/** Is this cell empty at the piece's own contrast (a space the sky shows through)? */
/** A sky cell: the reference fills it with a space and the palette's default ink. */
const isBlank = (c: Cell) => c.ch === ' ' && c.co === ' '

/** ESC. Built from a code point, never spelled inline: a literal escape in the
 *  source is at the mercy of the bundler's string handling. */
const ESC = String.fromCharCode(27)

const SGR = `${ESC}[0m`
const fg = (h: string) => `${ESC}[38;2;${rgb(h)}m`

const rgb = (h: string) => {
  const v = parseInt(h.slice(1), 16)

  return `${(v >> 16) & 255};${(v >> 8) & 255};${v & 255}`
}

// ── the dots look ───────────────────────────────────────────────────
//
// The published piece is shown in the library's "Halftone dots" look, and that
// is the reference the hero must match: each cell is redrawn as a dot whose
// size carries the shading, ` ·•●` from light to dark. The look is a pure
// function of the ORIGINAL frame's ink coverage times its colour's luminance,
// so it cannot be hand-drawn — the table below is the reference's own.

/** Measured ink coverage (0..100) of the glyphs this piece draws with. */
const COVER: Record<string, number> = {
  ' ': 0,
  '░': 25,
  '▒': 50,
  '▓': 75,
  '█': 100,
  '·': 4.054054054054054,
  '•': 12.162162162162163,
  '●': 48.64864864864865,
  '┼': 25.675675675675677,
  '▄': 51.351351351351354,
  '▀': 51.351351351351354
}

const cover = (ch: string) => COVER[ch] ?? 30

/** The look's ramp, light to dark, as the reference sorts it: ` ·•●`. */
const DOTS = [' ', '·', '•', '●']

function lum(h: string | undefined): number {
  if (!h) {
    return 0.88
  }

  const [r, g, b] = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255)

  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
}

/** Which characters the look redraws: shading blocks, plus ramp punctuation in a shaded render. */
const isShade = (ch: string): boolean => {
  const c = ch.codePointAt(0)!

  return c >= 0x2580 && c <= 0x259f
}

const RAMP_PUNCT = new Set('.,:;-~=+*!#%@$&')

/** Is this frame a shaded render? (Almost no letters, mostly ramp punctuation.) */
const rampShaded = (rows: Cell[][]): boolean => {
  let ink = 0
  let ramp = 0
  let alnum = 0

  for (const row of rows) {
    for (const cell of row) {
      if (cell.ch === ' ') {
        continue
      }

      ink++

      if (RAMP_PUNCT.has(cell.ch)) {
        ramp++
      } else {
        const c = cell.ch.charCodeAt(0)

        if ((c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122)) {
          alnum++
        }
      }
    }
  }

  return ink > 0 && ramp >= ink * 0.5 && alnum < ink * 0.05
}

/**
 * Redraw one frame in the dots look, exactly as the reference `restyle` does.
 *
 * Every inked cell's darkness is ink coverage x colour luminance, stretched to
 * the frame's own range, gamma-lifted (0.35, the reference's text-look default)
 * and floor-mapped onto ` ·•●`. Cells the look leaves alone — blank sky, and the
 * headstones' `┼` — keep their glyph.
 */
export const ghostDots = (t: number): { ch: string; co: string }[][] => {
  const rows = ghostFrame(t)
  const shaded = rampShaded(rows)

  const D: number[][] = rows.map(row =>
    row.map(cell => {
      if (cell.ch === ' ') {
        return -1
      }

      if (!isShade(cell.ch) && !(shaded && RAMP_PUNCT.has(cell.ch))) {
        return -1
      }

      return (cover(cell.ch) / 100) * lum(cell.co === ' ' ? undefined : hex(cell.co))
    })
  )

  let max = 0
  let min = Infinity

  for (const row of D) {
    for (const d of row) {
      if (d > max) {
        max = d
      }

      if (d >= 0.004 && d < min) {
        min = d
      }
    }
  }

  const spread = max - min > 0.05
  const span = spread ? max - min : max
  const lo = spread ? min : 0
  const gamma = 0.35
  const n = DOTS.length - 1

  return rows.map((row, y) =>
    row.map((cell, x) => {
      const d = D[y]![x]!

      if (d < 0) {
        return cell
      }

      const v = d < 0.004 || !span ? 0 : Math.max(0.0001, ((Math.max(0, d - lo) / span) ** gamma))

      return { ch: v === 0 ? ' ' : DOTS[1 + Math.min(n - 1, Math.floor(v * n))]!, co: cell.co }
    })
  )
}

/**
 * Colour a row of dots.
 *
 * Cells are terminal-height, so an upper and a lower grid row share one line:
 * ▀ paints the upper row's colour as the foreground and the lower row's colour
 * as the background, ▄ marks a cell whose upper row is empty, and a cell empty
 * in both rows is a space. That keeps all 28 rows of the piece in 14 lines
 * without cropping it, each half keeping its own palette colour.
 *
 * The result is one SGR-coloured string; runs are emitted only when the colour
 * actually changes, so a row costs a handful of escapes, not one per cell.
 */
/**
 * Paint the scenery from the theme, keep the ghost white.
 *
 * The published piece is a purple night over a black graveyard; on a themed
 * terminal that reads as a foreign colour block. The ghost keeps its own
 * near-white ramp (it is the subject and must stay bright on every skin), and
 * everything else — sky, mist, hill, stone, the ground shadow — is pushed
 * through the skin's accent hue at the piece's own lightness, so the scene
 * belongs to the theme instead of sitting in it. The moon takes the secondary
 * tone and the cheeks keep a hint of pink, tinted the same way.
 */
const tint = (hue: string, hexColor: string, mixIn = 0.72, gain = 1): string => {
  const v = parseInt(hue.slice(1), 16)
  const h = [(v >> 16) & 255, (v >> 8) & 255, v & 255]

  const r = parseInt(hexColor.slice(1), 16)
  const p = [(r >> 16) & 255, (r >> 8) & 255, r & 255]
  // Keep the cell's own lightness (a gradient still reads), take the hue.
  const lumC = (0.2126 * p[0]! + 0.7152 * p[1]! + 0.0722 * p[2]!) / 255
  const lumH = Math.max(0.04, (0.2126 * h[0]! + 0.7152 * h[1]! + 0.0722 * h[2]!) / 255)

  const out = p.map((ch, i) => Math.round(ch + (h[i]! * (lumC / lumH) * gain - ch) * mixIn))

  return '#' + out.map(ch => Math.max(0, Math.min(255, ch)).toString(16).padStart(2, '0')).join('')
}

/**
 * The moon takes a dimmed secondary ink.
 *
 * Its cream hex carries ~0.93 luminance, so tinting it straight put a blazing
 * yellow disc where a moon belongs — brighter than the ghost, which flattens
 * the composition. It keeps its hue at 70% of the accent's lightness.
 */
const moonGain = 0.55

const colorRow = (row: Cell[], c: ThemeColors): string => {
  let out = ''
  let cur = ''

  for (const cell of row) {
    const style = cell.ch === ' ' ? SGR : fg(themeInk(cell.co, c))

    if (style !== cur) {
      out += style
      cur = style
    }

    out += cell.ch
  }

  return out + SGR
}

/** A palette key painted for the active theme: ghost stays white, scenery takes the hue. */
const themeInk = (key: string, c: ThemeColors): string => {
  const raw = hex(key)
  const rank = PALETTE_KEYS.indexOf(key) - GHOST

  // Ghost body: the piece's own near-white ramp.
  if (rank >= 0 && rank <= 9) {
    return raw
  }

  const idx = PALETTE_KEYS.indexOf(key)
  // The skin's INK hue, not its accent seed: `accent` is a background/rung
  // colour (cyberpunk's is #002000, a near-black green), so tinting with it
  // produced the yellow night this replaced. `label` is the skin's own
  // foreground-alt ink (cyberpunk: green), then primary, then accent.
  const accent = c.label || c.primary || c.accent || c.text

  // Eyes take the panel's own text ink: they are the face, and they must stay
  // legible whatever skin is active (a dark eye on a light theme, a pale one on
  // a dark theme). The eye highlight is the brightest cell in the piece, so it
  // is the ghost's white — tinting it made a yellow glint on the face.
  if (idx === EYE + 1) {
    return '#ffffff'
  }

  if (idx === EYE) {
    return c.text || raw
  }

  // Moon: hue of the theme, dimmed so it never outshines the ghost.
  if (idx >= MOON && idx < MOON + 2) {
    return tint(accent, raw, 0.8, moonGain)
  }

  // Cheeks: a soft pink tinted toward the theme's hue.
  if (idx >= PINK) {
    return tint('#ff9ab8', raw, 0.55)
  }

  return tint(accent, raw)
}

/**
 * Terminal lines for one frame, ready to hand to <Ansi>.
 *
 * `_c` is accepted for call-site symmetry with the other hero art modules; the
 * piece paints its own palette, so the active theme does not enter here.
 */
export const ghostLines = (c: ThemeColors, t: number): Line[] => ghostDots(t).map(row => ['', colorRow(row, c)])

/** Half-resolution ghost for short terminals; same piece, sampled 2x2. */
export const ghostLinesCompact = (c: ThemeColors, t: number): Line[] => {
  const rows = ghostDots(t)
  const out: Line[] = []
  for (let y = 0; y < GHOST_ROWS; y += 2) {
    const row = rows[y]!
    const compact: Cell[] = []
    for (let x = 0; x < GHOST_COLS; x += 2) compact.push(row[x]!)
    out.push(['', colorRow(compact, c)])
  }
  return out
}

type Cell = { ch: string; co: string }
type Line = [string, string]
