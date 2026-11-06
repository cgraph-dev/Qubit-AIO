# WIP — inline TUI resize splice (vendored qubit-ink)

**Status:** open. This file records the investigation for the remaining four
`test_resize_ledger.py` cells. No renderer change is shipped: every attempted fix
either duplicated or lost transcript rows, so the four cells are still red on
`e2e/tui-resize` (base `fix/e2e-final` `bd36a2c5e`).

## Scope

Only the inline (main-screen) cells fail. Alternate-screen stays green.

```
cd qubit_runtime
TMPDIR=/tmp PYTHONPATH="$PWD:$PWD/.." \
  /home/trick/Projects/Repos/Qubit/.venv/bin/python -m pytest \
  tests/e2e/core/tui_pty/test_resize_ledger.py -k inline -q
# 4 failed, 7 passed, 11 deselected
```

Failing:

- `inline:midstream_drag_ledger`
- `inline:midstream_drag_width`
- `inline:midstream_two_step_shrink_ledger`
- `inline:persisted_matches_screen`

Passing: the four idle cells, `inline:midstream_two_step_shrink_width`,
`prompts_once`, `exits_clean`.

Measured failures vary by race/load but share one shape: a block of the reply is
duplicated near the start and a block from the tail is missing, plus at least one
row where the status rule and a paragraph row are spliced together, e.g.

```
lost=['r2w088'…] (22); dup=['r2w004'…] (27)
E   r2w188 r2w189 r2w190 r2w191 r2w192 r2w193 r2w194 r2w195 r2w196 r2w097 r2w098 r2w099
E   ─  r2w138 … r2w149                         e0/…
```

## What the renderer actually emits

`pty.log` from the failing run (tmux `pipe-pane`) contains eight `ESC[3J` and each
resize full reset is exactly:

```
ESC[2J ESC[3J ESC[H   (getClearTerminalSequence)
```

A standalone tmux probe confirms tmux honours 3J (24 scrollback rows → 4 after the
sequence), so the clear is not being ignored.

## The app frame is correct; the physical terminal is not

Temporary instrumentation (reverted) logged, per frame, the width/height and the
full text of `frame.screen` plus the diff patch types. On the last frame of the
failing `midstream_drag_ledger` run (W=90, H=76, 593 frames):

```
r1w000:1  r2w000:1  r2w087:1  r1w005:1
```

i.e. the virtual frame holds every token exactly once. The physical capture does
not. Therefore this is a **vendored renderer / physical-terminal desync**, not an
app-layout or virtualization-range bug and not a wrong test.

## Attempted fixes and measured outcome (all reverted)

1. **Main-screen resize: clear visible screen only, then reprint the whole frame**
   (`ERASE_SCREEN + CURSOR_HOME`, no 3J). 10 failed; 1692 screen words vs 392
   persisted (duplication) because tmux's reflowed scrollback is retained *and*
   reprinted.
2. **Main-screen resize: repaint only the last viewport rows**
   (`startY = H - V + 1`, no 3J). 10 failed; 217/232 words duplicated — the
   scrollback boundary after tmux reflow is not `H - V + 1` for the new width.
3. **Force full mount of the virtual history** (return `start=0,end=n`, no
   spacers) to rule out virtualization. Same four cells fail with the same
   splice, so the mounted window is not the cause.
4. **Defer the resize repaint until the app's width commit** (skip the
   `handleResize` microtask render for main-screen width changes). No change.
5. **Mark `prevFrameContaminated` on main-screen resize** (force a no-blit full
   render). No change.
6. **End each reset row with `CSI 1G` instead of CR** (cancel pending-wrap to
   avoid a double advance on full-width status rows). No change.
7. **160 ms settle + forced deep full reset after the burst.** No change; 15 ×
   `ESC[3J` instead of 8, and streaming diffs desync again immediately after the
   heal.

The app's own `useVirtualHistory` freeze/remount logic was also exercised; the
frame text stayed correct throughout.

## Proposed upstream patch

The main-screen branch assumes the terminal's scrollback depth matches
`H - V + 1` after a reset and then uses relative cursor moves. On a width change
with concurrent streaming, tmux reflows the buffer and the depth does not match,
so every following relative move lands a row off. A safe patch needs all three:

1. Add a main-screen frame reset to `ink.tsx` (analogous to
   `resetFramesForAltScreen`) that seeds `frontFrame`/`backFrame` with a blank
   screen of the new width, sets `prevFrameContaminated`, and clears
   `displayCursor`, *after* the app's width commit has landed.
2. In `LogUpdate.render`, for a main-screen resize, emit `ERASE_SCREEN +
   CURSOR_HOME` and repaint the **full frame** (all rows the frame contains); do
   not emit `ESC[3J`, and do not rely on a guessed viewport slice.
3. Re-anchor the physical cursor explicitly after the repaint — e.g. append
   `CSI <rows>;1H` — so the next `moveCursorTo` uses a known cursor, instead of
   trusting the CR/LF scroll arithmetic.

That combination must be validated by a PTY cell that streams a reply through a
rapid drag; unit-level diffs cannot observe terminal reflow. A regression test
for the scrollback contract is straightforward at the unit level
(`clearTerminal.eraseScrollback === false` on a main-screen resize, `true` on
alt-screen) but is not sufficient to prove the splice is gone.

## Rebuild note

The committed TUI bundle is not byte-reproducible from a clean checkout here: a
rebuild of unchanged sources records the same `sourceHash` but a different
`outputHash`. Any renderer change must rebuild `ui-tui/dist/entry.js` and commit
the regenerated `hermes-build.json` in the same change.

## Classification

- **Vendored:** `qubit_runtime/ui-tui/packages/qubit-ink/src/ink/log-update.ts`,
  `.../ink.tsx`, `.../clearTerminal.ts`, `.../terminal.ts`.
- **Product app:** correct — the last frame has every word once.
- **Test:** correct — alternate-screen passes; inline is the documented
  main-screen (dashboard) mode.
