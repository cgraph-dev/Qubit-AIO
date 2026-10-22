# Orb FX Spec — Qubit-mark animation contract

The desktop orb wears the Qubit mark. Two renderers draw the same frame and
must stay in visual agreement:

| Renderer | Entry point | Proof surface |
| --- | --- | --- |
| Tk (fallback) | `voicebridge/orb/mark.py` `render(canvas, cx, cy, radius, t, state, reduced_motion, ...)` | any canvas with `create_oval`/`create_line` |
| Qt (default) | `voicebridge/orb/qt_app.py` `_paint_mark(p, cx, cy, R, t, state, brightness, family)` | offscreen `QImage` + `QPainter` |

Geometry constants are owned by `mark.py` (`RX_FRAC=0.72`, `RY_FRAC=0.28`,
`TILT_A=30`, `TILT_B=-30`, `NUCLEUS_FRAC=0.050`, `FRINGE_PX_AT_96=0.7`,
`RING_PERIOD_NEAR=23.0`, `RING_PERIOD_FAR=31.0`, `RING_SWAY_DEG=6.0`, nine
`PARTICLES`). Qt translates those constants to `QPainter`; it must not invent
its own.

## v3 — per-element spin (owner 2026-10-22)

> "the circles should spin, particles should have multiple spin animations,
> random particles, spiral spin, reverse spin against each other to create
> illusion, not the whole voice orb is a single image that rotates, each element
> should be alive and rotate"

The whole-mark X+Y **tumble is retired** (`spin_angles()` returns `(0.0, 0.0)`),
because rotating the entire assembly as one rigid picture is exactly the
"single image that rotates" reading. Motion now comes from independent
per-element sources:

| Layer | Motion | Periods |
|---|---|---|
| Circles | full 360° turn about the nucleus, per-ring | `RING_SPIN_PERIODS_S` 6.9–25.45 s |
| Circle direction | counter-rotating pairs (`orbit=a` +1 / `orbit=b` −1), satellites alternating, inner reversed | — |
| Logo beads | orbit + own-axis spin + spiral radial play + micro-wobble | orbit 6.4–18.9 s, spin `PARTICLE_SPIN_PERIODS_S` 1.6–5.25 s |

**The mechanism that matters.** An ellipse is symmetric under its own parameter
shift, so re-tilting a ring never looks like rotation. A circle only visibly
spins when the **starting phase of the stroke walks round the ellipse** —
`ring_path` samples `orbit_point(..., 360*i/n + spin_deg)`. Implementing "spin"
as a tilt offset produces animation that measures as motion and looks frozen.

All declared spin periods are checked non-harmonic against each other, against
the ring/shell/inner periods and across the two tables, so nothing beats in
lock-step. Radius scaling stays SHARED across all rings (the 2026-10-18
"same axis / proportional" ruling survives); only the rotation is per-ring.

Measured at R=100: ring point travel went from **7.5 px over 40 s** (the old
±7° sway) to **125–186 px**.

## v2 — the dense field (owner 2026-10-17: "more particles, more 3D")

All motion now comes from ONE module, `voicebridge/orb/mark_field.py`, consumed
by both renderers (Qt live orb + Tk fallback); the painters contain no geometry.

| Layer | What it adds | Period / range |
|---|---|---|
| Camera sway | whole mark floats (`camera_offset`) | 0.27 / 0.19 Hz, ±1.6% R |
| Precession | orbit tilts drift as well as sway | ±9°, 41 s / 57 s |
| Heartbeat | lub-dub nucleus + thin halo | 0.95 s, 2nd thump 62% |
| Shockwave | thin ring expands out of the nucleus | 0.5 s, 1.0→2.3× |
| Particles | 9 logo beads, depth size/alpha/lift/parallax + 6-sample trails | 6.4–18.9 s |
| Dust | 22 slow motes on the same orbits | 5–14 s |
| Comet | one fast traveller, 12-sample tapered tail | every 7 s, 2.4× |
| Glints | 26 deterministic sparkles | life 1.1–2.15 s, first 45% visible |

Depth ordering is the 3-D read: every `depth < 0.5` element draws before the
nucleus, every `depth >= 0.5` element after it. Reduced motion freezes `t=0`
and hides the comet, trails, glints, dust and shockwave.

Contract bounds were reconciled when density increased: the visible-primitive
delta cap is 40 (26 glints + comet tail) and the perf test asserts a bounded
per-frame SPREAD (120) rather than comparing batch halves, because the comet's
periodic sweep makes half-comparison a phase lottery.

## Effects

All effects are pure functions of the frame clock `t` — **no randomness** — so
the same `t` always yields the same frame on both renderers.

**Rings.** Two tilted ellipse orbits (`±30°`) that sway `RING_SWAY_DEG` around
their tilt on independent periods (23 s / 31 s). Far orbit drawn first, then
nucleus, then near orbit (z-order).

**Heartbeat nucleus.** The centre disc pulses with period **0.95 s** and a
double-thump envelope (lub-dub). Its drawn size and opacity peak at the beat
phase; the envelope repeats every 0.95 s. In this worktree the base nucleus is
a constant disc — the pulse is the sibling change (see *Merge status*).

**Particle depth (3D).** Each bead's orbit angle gives
`depth = 0.5 + 0.5 * cos(angle)` (1 near the viewer, 0 far). Depth drives, on
both renderers, **scale**, **alpha/contrast**, and a **white-lift**:
far beads are smaller and lower-contrast than near ones at the same phase. The
far/near fields are **z-split around the nucleus** (far beads behind it, near
beads in front) and the two fields **parallax** against each other as beads
travel their orbit.

**Particle trails.** Each bead leaves **four fading samples** of its recent
positions (older samples smaller and more transparent), so motion smears
instead of jumping.

**Sparkles.** **14 deterministic glints** positioned from the mark's own
geometry, never `random`. Each has life **1.1–2.15 s** and is **visible for the
first 45 %** of its cycle; the count of live glints per frame is bounded
(≤ 20).

**Reduced motion.** When reduced motion is on, the animation clock is frozen at
phase 0 (`tt = 0`) and **no effect animates**: two different `t` values must
produce a byte-identical frame that is still a complete mark (not blank). Tk
reads the `reduced_motion` argument; Qt reads `QUBIT_REDUCED_MOTION`
(`1/true/yes/on`).

## Renderer agreement (the contract)

For an identical `(cx, cy, radius, t, state)` the two renderers must agree on
the *behaviour*, not the exact bytes: motion between nearby phases, a nucleus
pulse enclosing the same 0.95 s period, the depth→(size, contrast, lift)
coupling, presence/absence of glints, and time-invariance under reduced motion.
The harness below is deliberately renderer-agnostic: it records primitives
(centre, radius, colour, alpha) from each renderer and asserts these
relationships on both.

## Measurement method

`tests/test_voice_orb_fx_contract9u.py` — parametrised over `tk` and `qt`:

- **Tk** draws through a `FakeCanvas` recorder (no display, no Tk).
- **Qt** draws offscreen (`QT_QPA_PLATFORM=offscreen`) into a `QImage` through a
  recording `QPainter` proxy that forwards every op to the real painter while
  logging it. No window is created.

Assertions (each runs against both renderers):

1. **Motion** — recorded geometry differs between `t=2.00` and `t=2.05`.
2. **Heartbeat** — 40 phases over `[0, 1.9)`; nucleus size×opacity pulses and
   shows exactly **two** peaks whose circular separation is ~0.95 s.
3. **Depth** — at one phase, larger (near) beads have higher contrast than
   smaller (far) beads, and bead radii are non-uniform.
4. **Sparkles** — total primitives vary across the cycle (glints appear and
   hide) and the extra glint count is `≤ 20`.
5. **Reduced motion** — identical primitives for two different `t`.

Where a renderer lacks an effect in the current worktree, the test is
`xfail(strict=False)` with the effect named, so the suite is green now and the
test flips to a real pass once the effect is merged. Assertions are never
weakened to force a pass.

`tests/test_voice_orb_fx_perf9u.py` — renders **120 Qt frames offscreen**,
times the batch as a whole (never per frame), and asserts the **mean** cost is
under **25 ms/frame** and that per-frame primitives stay within a fixed bound
(`≤ 300`) and do not drift upward across the batch.

`scripts/orb_fx_sheet.py` — renders the Qt mark at six phases into a PNG contact
sheet (offscreen, offline) for a human eyeball check:

```
python scripts/orb_fx_sheet.py --out orb_fx_sheet.png
```

Run the harness with the project venv (it has pytest, PySide6 and PIL):

```
.venv/bin/python -m pytest tests/test_voice_orb_fx_contract9u.py tests/test_voice_orb_fx_perf9u.py -q
```

## Merge status (this worktree)

The base mark (sway/orbit motion, depth-scaled beads, chromatic fringe,
reduced-motion freeze) is present and passes. The **heartbeat nucleus**,
**trails** and **sparkles** are sibling changes not yet in this worktree; the
heartbeat and sparkle contract tests are therefore `xfail` here and become real
passes once the two renderers gain those effects.
