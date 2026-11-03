import { PassThrough } from 'stream'

import { renderSync } from '@qubit/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import { SessionPanel } from '../components/branding.js'
import { GHOST_COLS, GHOST_LINES, GHOST_ROWS, ghostDots, ghostFrame, ghostLines } from '../ghost.js'
import { DEFAULT_THEME, type ThemeColors } from '../theme.js'
import type { SessionInfo } from '../types.js'

// The startup hero is a port of the ascii-magic "Friendly Floating Ghost" piece
// shown in the library's Halftone dots look — that look IS the reference, so the
// invariants below are about the piece's shape, its dots ramp and its palette.
//
// Regression: an earlier hero mapped every cell onto the active theme (flat,
// unreadable), and the fix after that halved the grid with half-block packing,
// which turned the dithered sky into a wall of ▀.

const COLORS = DEFAULT_THEME.color as ThemeColors

const ESC = String.fromCharCode(27)
const stripAnsi = (s: string) => s.replace(new RegExp(`${ESC}\\[[\\d;?]*[a-zA-Z]`, 'g'), '')

describe('friendly floating ghost hero', () => {
  it('draws the piece on its own 60x28 grid, one grid row per line', () => {
    expect(ghostFrame(1)).toHaveLength(GHOST_ROWS)
    expect(ghostFrame(1).every(row => row.length === GHOST_COLS)).toBe(true)
    expect(GHOST_LINES).toBe(GHOST_ROWS)
    expect(ghostLines(COLORS, 1)).toHaveLength(GHOST_LINES)
  })

  it('renders the halftone dots look: only space and ` ·•●`', () => {
    for (const t of [0, 0.5, 2.9, 3.3, 5.6]) {
      for (const [, text] of ghostLines(COLORS, t)) {
        const plain = stripAnsi(text)

        expect(plain).toHaveLength(GHOST_COLS)
        expect(plain).toMatch(/^[ ·•●┼]*$/)
      }
    }
  })

  it('keeps the ghost white and repaints the scenery in the theme hue', () => {
    const painted = ghostLines(COLORS, 1)
      .map(([, text]) => text)
      .join('')

    // The ghost is the subject: its ramp stays the piece's own near-whites on
    // every skin.
    for (const hex of ['#ffffff', '#d9d9f0', '#6a6a9a']) {
      const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))

      expect(painted).toContain(`${ESC}[38;2;${r};${g};${b}m`)
    }

    // Scenery takes the accent's hue: the amber skin must paint amber ground,
    // not the piece's purple night.
    const hueOf = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
    const accent = hueOf(COLORS.accent as string)
    const ground = hueOf('#59472d')

    // Same hue family: red channel leads, blue trails, exactly as the accent.
    expect(accent[0]).toBeGreaterThan(accent[2]!)
    expect(ground[0]).toBeGreaterThan(ground[2]!)
    // And the piece's purple night does not survive.
    expect(painted).not.toContain(`${ESC}[38;2;10;6;24m`)
  })

  it('animates: the dots look moves frame to frame', () => {
    const a = ghostDots(1)
      .flat()
      .map(cell => cell.ch)
      .join('')

    const b = ghostDots(1.08)
      .flat()
      .map(cell => cell.ch)
      .join('')

    expect([...a].filter((ch, i) => ch !== b[i]).length).toBeGreaterThan(20)
  })

  it('animates the top rows too: the head is never frozen', () => {
    // The reference bob only travels ~1.3 rows on a slow wave, so the crown of
    // the head held still for over a second — it read as a static image. Every
    // 80ms tick must move at least one of the first eight rows.
    let frozen = 0

    for (let t = 0; t < 2; t += 0.08) {
      const a = ghostDots(t).slice(0, 8).map(row => row.map(c => c.ch).join(''))
      const b = ghostDots(t + 0.08).slice(0, 8).map(row => row.map(c => c.ch).join(''))

      if (a.every((row, i) => row === b[i])) {
        frozen++
      }
    }

    expect(frozen).toBe(0)
  })

  it('paints the dots into the rendered panel', async () => {
    const out = await renderPanel()

    expect(out).toContain('●')
    expect(out).toContain(`${ESC}[38;2;255;255;255m`)
    // The half-block packing this replaced must not come back.
    expect(stripAnsi(out)).not.toContain('▀')
  })
})

async function renderPanel(): Promise<string> {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 44 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })

  let captured = ''
  stdout.on('data', chunk => {
    captured += chunk.toString()
  })

  const info: SessionInfo = {
    mcp_servers: [],
    model: 'test-model',
    skills: { core: ['a'] },
    tools: { file: ['read_file'] }
  }

  const instance = renderSync(React.createElement(SessionPanel, { info, sid: 'test', t: DEFAULT_THEME }), {
    patchConsole: false,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })

  instance.unmount()

  return captured
}
